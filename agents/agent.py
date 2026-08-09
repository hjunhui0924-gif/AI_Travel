import json
import hashlib
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from langgraph.checkpoint.sqlite import SqliteSaver

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
except Exception:
    chromadb = None
    ChromaSettings = None
    DefaultEmbeddingFunction = None
    OpenAIEmbeddingFunction = None

from utils.oss_utils import delete_oss_object
from utils.stock_utils import format_hs_stock_item, get_hs_index_item, get_hs_stock_item, has_juhe_stock_key
from utils.weather_utils import current_cn_datetime, format_weather_text, has_amap_key
from agents.schemas import TravelPlan
from agents.travel_agent import plan_travel, render_travel_response
from services.travel_store import (
    delete_travel_thread,
    get_current_plan,
    list_conversation_turns,
    list_travel_turns,
    save_plan_version,
    save_travel_turn,
)
from services.travel_search import _is_denied_source, _normalize_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
RESOURCES_DIR = BASE_DIR / "resources"
RESOURCES_DIR.mkdir(exist_ok=True)
DB_PATH = RESOURCES_DIR / "ai_agent_threads.db"
CN_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_THREAD_TITLE = "新会话"
ATTACHMENT_START = "<ATTACHMENT_CONTEXT>"
ATTACHMENT_END = "</ATTACHMENT_CONTEXT>"
SEARCH_START = "<SEARCH_CONTEXT>"
SEARCH_END = "</SEARCH_CONTEXT>"
ACTIVITY_START = "__ACTIVITY__"
ACTIVITY_END = "__END_ACTIVITY__"
ASSISTANT_META_START = "__ASSISTANT_META__"
ASSISTANT_META_END = "__END_ASSISTANT_META__"
MAX_RELEVANT_CHUNKS = 6
CHROMA_DIR = RESOURCES_DIR / "chroma_runtime"

_activity_log_var: ContextVar[list[dict] | None] = ContextVar("activity_log", default=None)
_source_cards_var: ContextVar[list[dict] | None] = ContextVar("source_cards", default=None)
_travel_plan_var: ContextVar[dict | None] = ContextVar("travel_plan", default=None)

_chroma_client = None
_chroma_embedding_function = None


def _today_cn() -> date:
    return datetime.now(CN_TZ).date()


def _now_cn_label() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _activity_log() -> list[dict]:
    log = _activity_log_var.get()
    if log is None:
        log = []
        _activity_log_var.set(log)
    return log


def _source_cards() -> list[dict]:
    cards = _source_cards_var.get()
    if cards is None:
        cards = []
        _source_cards_var.set(cards)
    return cards


def _reset_runtime_buffers() -> None:
    _activity_log_var.set([])
    _source_cards_var.set([])
    _travel_plan_var.set(None)


def _log_activity(stage: str, title: str, detail: str = "", state: str = "completed") -> None:
    if stage == "attachment" and "未附加文件" in title:
        return
    _activity_log().append(
        {
            "stage": stage,
            "title": title,
            "detail": detail,
            "state": state,
            "timestamp": _now_cn_label(),
        }
    )


def _log_source_card(
    title: str,
    url: str,
    summary: str = "",
    source_date: str = "",
    evidence_id: str = "",
) -> None:
    card = {
        "title": title,
        "url": url,
        "summary": summary,
        "source_date": source_date,
    }
    if evidence_id:
        card["evidence_id"] = evidence_id
    _source_cards().append(card)


def consume_activity_log() -> list[dict]:
    items = list(_activity_log())
    _activity_log_var.set([])
    return items


def consume_source_cards() -> list[dict]:
    items = list(_source_cards())
    _source_cards_var.set([])
    return items


def consume_travel_plan() -> dict | None:
    plan = _travel_plan_var.get()
    _travel_plan_var.set(None)
    return plan


def _resolve_model_settings() -> dict:
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or ""
    )
    base_url = (
        os.getenv("LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
    )
    model_name = os.getenv("LLM_MODEL")
    if not model_name:
        model_name = "qwen-plus" if os.getenv("DASHSCOPE_API_KEY") else "gpt-4.1-mini"

    return {
        "model": model_name,
        "model_provider": os.getenv("LLM_PROVIDER", "openai"),
        "base_url": base_url,
        "api_key": api_key,
        "temperature": 0.2,
    }


def _resolve_embedding_settings() -> dict:
    return {
        "api_key": (
            os.getenv("EMBEDDING_API_KEY")
            or os.getenv("LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or ""
        ),
        "base_url": (
            os.getenv("EMBEDDING_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("DASHSCOPE_BASE_URL")
            or None
        ),
        "model": (
            os.getenv("EMBEDDING_MODEL")
            or os.getenv("OPENAI_EMBEDDING_MODEL")
            or "text-embedding-3-small"
        ),
    }


def _get_chroma_embedding_function():
    global _chroma_embedding_function
    if _chroma_embedding_function is not None:
        return _chroma_embedding_function

    settings = _resolve_embedding_settings()
    if OpenAIEmbeddingFunction and settings["api_key"]:
        _chroma_embedding_function = OpenAIEmbeddingFunction(
            api_key=settings["api_key"],
            api_base=settings["base_url"],
            model_name=settings["model"],
        )
        return _chroma_embedding_function

    if DefaultEmbeddingFunction:
        _chroma_embedding_function = DefaultEmbeddingFunction()
        return _chroma_embedding_function

    return None


def _get_chroma_client():
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    if not chromadb or not ChromaSettings:
        return None

    CHROMA_DIR.mkdir(exist_ok=True)
    _chroma_client = chromadb.Client(
        ChromaSettings(
            is_persistent=True,
            persist_directory=str(CHROMA_DIR),
            anonymized_telemetry=False,
        )
    )
    return _chroma_client


model = init_chat_model(**_resolve_model_settings())

_raw_web_search = None


def _get_raw_web_search():
    """Create the web search client only after a request opts into search."""

    global _raw_web_search
    if _raw_web_search is not None:
        return _raw_web_search
    if TavilySearch is None or not os.getenv("TAVILY_API_KEY"):
        return None
    try:
        _raw_web_search = TavilySearch(
            max_results=6,
            topic="general",
            include_images=False,
            include_answer=False,
            include_raw_content=False,
            search_depth="advanced",
            handle_tool_error=True,
            handle_validation_error="搜索参数无效，请简化关键词后重试。",
        )
    except Exception as exc:
        _log_activity("tool", "联网搜索初始化失败", str(exc))
        return None
    return _raw_web_search


def _is_time_sensitive_query(query: str) -> bool:
    keywords = [
        "最新",
        "今天",
        "今日",
        "当前",
        "现在",
        "实时",
        "latest",
        "today",
        "current",
        "now",
        "live",
    ]
    lowered = query.lower()
    return any(keyword in lowered for keyword in keywords) or any(keyword in query for keyword in keywords)


def _is_market_data_query(query: str) -> bool:
    keywords = [
        "上证",
        "深证",
        "a股",
        "沪深",
        "恒生",
        "纳指",
        "道指",
        "指数",
        "股价",
        "行情",
        "成交额",
        "stock",
        "index",
        "market",
        "price",
    ]
    lowered = query.lower()
    return any(keyword in lowered for keyword in keywords) or any(keyword in query for keyword in keywords)


def _extract_dates(text: str) -> list[date]:
    candidates = []
    patterns = [
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            try:
                year, month, day = map(int, match)
                candidates.append(date(year, month, day))
            except ValueError:
                continue
    return candidates


def _search_queries(query: str) -> list[str]:
    today = _today_cn()
    queries = [query]
    if _is_time_sensitive_query(query):
        queries.extend(
            [
                f"{query} {today.isoformat()}",
                f"{query} {today.year}",
                f"{query} today",
            ]
        )
    deduped = []
    for item in queries:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _is_weather_query(query: str) -> bool:
    keywords = [
        "天气",
        "气温",
        "下雨",
        "降雨",
        "温度",
        "风力",
        "湿度",
        "weather",
        "forecast",
        "temperature",
        "rain",
    ]
    lowered = query.lower()
    return any(keyword in lowered for keyword in keywords) or any(keyword in query for keyword in keywords)


def _is_forecast_query(query: str) -> bool:
    keywords = ["预报", "明天", "后天", "未来", "forecast", "tomorrow"]
    lowered = query.lower()
    return any(keyword in lowered for keyword in keywords) or any(keyword in query for keyword in keywords)


def _extract_weather_location(query: str) -> str:
    known_locations = [
        "上海",
        "北京",
        "广州",
        "深圳",
        "杭州",
        "苏州",
        "南京",
        "成都",
        "重庆",
        "武汉",
        "西安",
        "天津",
    ]
    for location in known_locations:
        if location in query:
            return location
    cleaned = query
    for token in ["今天天气", "今日天气", "天气", "气温", "预报", "实时", "最新", "明天", "后天"]:
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "上海"


@tool
def current_datetime() -> str:
    """Get the current date and time in Asia/Shanghai for time-sensitive reasoning."""
    now = current_cn_datetime()
    return (
        f"当前日期: {now['date']}\n"
        f"当前时间: {now['time']}\n"
        f"当前时区: {now['timezone']}\n"
        f"星期: {now['weekday']}"
    )


def weather_lookup_impl(location: str, forecast: bool = False) -> str:
    if not has_amap_key():
        _log_activity("tool", "天气接口不可用", "未配置 AMAP_WEB_API_KEY")
        return "当前未配置高德天气 API Key，无法调用天气接口。"

    mode = "天气预报" if forecast else "实时天气"
    _log_activity("tool", f"调用{mode}接口", location, state="running")
    try:
        text = format_weather_text(location, forecast=forecast)
    except Exception as exc:
        _log_activity("tool", f"{mode}接口失败", str(exc))
        return f"{mode}查询失败: {exc}"

    _log_activity("tool", f"{mode}接口完成", location)
    return text


@tool
def weather_lookup(location: str, forecast: bool = False) -> str:
    """Get current weather or forecast for a Chinese location. Use for weather questions before falling back to web search."""
    return weather_lookup_impl(location, forecast)


def _collect_query_terms(query: str) -> set[str]:
    lowered = query.lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", query)
    bigrams = set("".join(chinese_chars[index:index + 2]) for index in range(len(chinese_chars) - 1))
    return {term for term in words.union(bigrams) if term.strip()}


def _score_chunk(query_terms: set[str], chunk_text: str) -> int:
    if not query_terms:
        return 0
    lowered = chunk_text.lower()
    score = 0
    for term in query_terms:
        occurrences = lowered.count(term.lower())
        if occurrences:
            score += occurrences * max(len(term), 1)
    return score


def _lexical_select_relevant_chunks(query: str, chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []
    query_terms = _collect_query_terms(query)
    scored = []
    for index, chunk in enumerate(chunks):
        score = _score_chunk(query_terms, chunk.get("text", ""))
        scored.append((score, index, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [chunk for score, _index, chunk in scored if score > 0][:MAX_RELEVANT_CHUNKS]
    return selected or chunks[: min(MAX_RELEVANT_CHUNKS, len(chunks))]


def _attachment_file_key(attachment: dict) -> str:
    payload = f"{attachment.get('name', '')}|{attachment.get('extension', '')}|{attachment.get('content', '')}"
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


def _select_relevant_chunks_via_chroma(query: str, attachment: dict) -> list[dict]:
    chunks = attachment.get("chunks", [])
    if not chunks:
        return []

    client = _get_chroma_client()
    embedding_function = _get_chroma_embedding_function()
    if client is None or embedding_function is None:
        return []

    collection_name = f"attachment_{uuid4().hex}"
    file_key = _attachment_file_key(attachment)
    chunk_lookup = {}
    ids = []
    documents = []
    metadatas = []
    for index, chunk in enumerate(chunks):
        chunk_id = f"{file_key}_{index}"
        ids.append(chunk_id)
        documents.append(chunk.get("text", ""))
        metadatas.append(
            {
                "file_key": file_key,
                "label": chunk.get("label", f"chunk_{index + 1}"),
                "chunk_index": index,
            }
        )
        chunk_lookup[chunk_id] = chunk

    collection = None
    try:
        collection = client.create_collection(name=collection_name, embedding_function=embedding_function)
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        result = collection.query(query_texts=[query], n_results=min(MAX_RELEVANT_CHUNKS, len(ids)))
    except Exception:
        if collection is not None:
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
        return []

    try:
        result_ids = (result.get("ids") or [[]])[0]
        selected = [chunk_lookup[item_id] for item_id in result_ids if item_id in chunk_lookup]
        return selected
    finally:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass


def _select_relevant_chunks(query: str, chunks: list[dict], attachment: dict | None = None) -> list[dict]:
    if attachment:
        selected = _select_relevant_chunks_via_chroma(query, attachment)
        if selected:
            return selected
    return _lexical_select_relevant_chunks(query, chunks)


def _parse_tencent_quote(content: str) -> dict | None:
    match = re.search(r'="([^"]+)"', content)
    if not match:
        return None
    parts = match.group(1).split("~")
    if len(parts) < 33:
        return None

    code = parts[2]
    name = parts[1]
    current = parts[3]
    prev_close = parts[4]
    open_price = parts[5]
    volume = parts[6]
    amount = parts[37] if len(parts) > 37 else parts[36] if len(parts) > 36 else ""
    timestamp = parts[30] if len(parts) > 30 else ""
    change = parts[31] if len(parts) > 31 else ""
    change_pct = parts[32] if len(parts) > 32 else ""
    high = parts[33] if len(parts) > 33 else ""
    low = parts[34] if len(parts) > 34 else ""

    return {
        "name": name,
        "code": code,
        "current": current,
        "prev_close": prev_close,
        "open": open_price,
        "volume": volume,
        "amount": amount,
        "timestamp": timestamp,
        "change": change,
        "change_pct": change_pct,
        "high": high,
        "low": low,
    }


def _fetch_tencent_quote(symbol: str) -> dict | None:
    url = f"https://qt.gtimg.cn/q={urllib.parse.quote(symbol)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    try:
        content = urllib.request.urlopen(request, timeout=20).read().decode("gbk", errors="replace")
    except Exception as exc:
        _log_activity("tool", "行情接口请求失败", str(exc))
        return None
    return _parse_tencent_quote(content)


def _market_symbol_from_query(query: str) -> str | None:
    mapping = {
        "上证指数": "sh000001",
        "上证": "sh000001",
        "上证综指": "sh000001",
        "深证成指": "sz399001",
        "深证": "sz399001",
        "创业板指": "sz399006",
        "恒生指数": "hkHSI",
    }
    for keyword, symbol in mapping.items():
        if keyword in query:
            return symbol
    return None


def _extract_stock_keyword(query: str) -> str:
    cleaned = query
    for token in ["今日", "今天", "最新", "实时", "行情", "股价", "股票", "A股", "a股"]:
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query


def _stock_gid_from_query(query: str) -> str | None:
    direct_map = {
        "南京银行": "sh601009",
        "招商银行": "sh600036",
        "工商银行": "sh601398",
        "中国平安": "sh601318",
        "贵州茅台": "sh600519",
    }
    for keyword, gid in direct_map.items():
        if keyword in query:
            return gid
    match = re.search(r"\b([sS][hzHZ]\d{6})\b", query)
    if match:
        return match.group(1).lower()
    match = re.search(r"\b(6\d{5}|0\d{5}|3\d{5})\b", query)
    if match:
        code = match.group(1)
        if code.startswith("6"):
            return f"sh{code}"
        return f"sz{code}"
    return None


def _hs_index_type_from_query(query: str) -> str | None:
    if "上证" in query or "上证指数" in query or "上证综指" in query:
        return "0"
    if "深证" in query or "深证成指" in query:
        return "1"
    return None


def fetch_hs_stock_snapshot(query: str) -> str | None:
    if not has_juhe_stock_key():
        return None

    index_type = _hs_index_type_from_query(query)
    if index_type is not None:
        _log_activity("tool", "调用聚合数据指数接口", f"type={index_type}", state="running")
        try:
            item = get_hs_index_item(index_type=index_type)
        except Exception as exc:
            _log_activity("tool", "聚合数据指数接口失败", str(exc))
            return None
        if not item:
            _log_activity("tool", "聚合数据指数接口无匹配", f"type={index_type}")
            return None
        _log_activity(
            "tool",
            "聚合数据指数接口返回",
            f"{item.get('name', '')} {item.get('nowPri', '')}（{item.get('date', '')} {item.get('time', '')}）".strip(),
        )
        _log_source_card(
            title=f"{item.get('name', '')} 沪深指数快照",
            url=f"https://web.juhe.cn/finance/stock/hs?type={index_type}",
            summary=f"最新价 {item.get('nowPri', '')}，涨跌幅 {item.get('increase', '')}%",
            source_date=item.get("date", ""),
        )
        return format_hs_stock_item(item)

    gid = _stock_gid_from_query(query)
    if not gid:
        return None

    _log_activity("tool", "调用聚合数据沪深接口", gid, state="running")
    try:
        item = get_hs_stock_item(gid=gid, stock_type="a", page=1)
    except Exception as exc:
        _log_activity("tool", "聚合数据沪深接口失败", str(exc))
        return None

    if not item:
        _log_activity("tool", "聚合数据沪深接口无匹配", gid)
        return None

    _log_activity(
        "tool",
        "聚合数据沪深接口返回",
        f"{item.get('name', '')} {item.get('nowPri', '')}（{item.get('date', '')} {item.get('time', '')}）".strip(),
    )
    _log_source_card(
        title=f"{item.get('name', '')} 沪深股市快照",
        url=f"https://web.juhe.cn/finance/stock/hs?gid={item.get('gid', '')}",
        summary=f"最新价 {item.get('nowPri', '')}，涨跌幅 {item.get('increase', '')}%",
        source_date=item.get("date", ""),
    )
    return format_hs_stock_item(item)


def fetch_market_snapshot(query: str) -> str | None:
    symbol = _market_symbol_from_query(query)
    if not symbol:
        return None

    _log_activity("tool", "调用行情快照接口", symbol, state="running")
    quote = _fetch_tencent_quote(symbol)
    if not quote:
        return None

    source_date = quote["timestamp"][:8] if quote.get("timestamp") else ""
    source_date_label = f"{source_date[:4]}-{source_date[4:6]}-{source_date[6:8]}" if len(source_date) == 8 else ""
    source_time_label = ""
    if quote.get("timestamp") and len(quote["timestamp"]) >= 14:
        ts = quote["timestamp"]
        source_time_label = f"{ts[8:10]}:{ts[10:12]}:{ts[12:14]}"

    _log_activity(
        "tool",
        "行情快照返回",
        f"{quote['name']} {quote['current']}（{source_date_label} {source_time_label}）".strip(),
    )
    _log_source_card(
        title=f"{quote['name']} 实时行情",
        url=f"https://gu.qq.com/{symbol}",
        summary=f"最新价 {quote['current']}，涨跌 {quote['change']}，涨跌幅 {quote['change_pct']}%",
        source_date=source_date_label,
    )

    return "\n".join(
        [
            "行情快照结果:",
            f"名称: {quote['name']}",
            f"代码: {quote['code']}",
            f"最新价: {quote['current']}",
            f"昨收: {quote['prev_close']}",
            f"今开: {quote['open']}",
            f"涨跌: {quote['change']}",
            f"涨跌幅: {quote['change_pct']}%",
            f"最高: {quote['high']}",
            f"最低: {quote['low']}",
            f"成交量: {quote['volume']}",
            f"成交额: {quote['amount']}",
            f"数据时间: {source_date_label} {source_time_label}".strip(),
            f"来源: https://gu.qq.com/{symbol}",
        ]
    )


def perform_web_search(query: str) -> str:
    hs_snapshot = None
    if _is_market_data_query(query):
        hs_snapshot = fetch_hs_stock_snapshot(query)

    market_snapshot = None
    if _is_market_data_query(query) and _is_time_sensitive_query(query) and not hs_snapshot:
        market_snapshot = fetch_market_snapshot(query)

    weather_result = None
    if _is_weather_query(query) and has_amap_key():
        location = _extract_weather_location(query)
        forecast = _is_forecast_query(query)
        _log_activity("think", "识别到天气问题", f"地点: {location}，模式: {'预报' if forecast else '实时'}")
        weather_result = weather_lookup_impl(location=location, forecast=forecast)

    raw_web_search = _get_raw_web_search()
    if not raw_web_search:
        if weather_result and "失败" not in weather_result:
            _log_activity("search", "未使用网页搜索", "已命中天气专用接口")
            return weather_result
        if hs_snapshot:
            _log_activity("search", "未使用网页搜索", "已命中聚合数据沪深股市接口")
            return hs_snapshot
        if market_snapshot:
            _log_activity("search", "未使用网页搜索", "已命中行情快照接口")
            return market_snapshot
        _log_activity("search", "跳过联网搜索", "未配置 Tavily API Key")
        return "当前未配置 Tavily 搜索能力，无法执行联网搜索。"

    today = _today_cn()
    time_sensitive = _is_time_sensitive_query(query)
    market_sensitive = _is_market_data_query(query)

    _log_activity(
        "think",
        "判断是否需要时效检查",
        "当前问题包含最新/实时特征，搜索结果会校验日期。" if time_sensitive else "当前问题时效性较弱，将按常规搜索处理。",
    )

    merged_results = []
    seen_urls = set()
    for search_query in _search_queries(query):
        _log_activity("search", "执行联网搜索", search_query, state="running")
        try:
            result = raw_web_search.invoke({"query": search_query})
        except Exception as exc:
            _log_activity("tool", "web_search 失败", str(exc))
            return f"联网搜索失败: {exc}"

        if not isinstance(result, dict) or "results" not in result or not isinstance(result["results"], list):
            _log_activity("tool", "web_search 返回格式无效", "结果缺少 results 列表")
            return "联网搜索失败：搜索服务返回格式无效。"

        for item in result["results"]:
            if not isinstance(item, dict):
                continue
            raw_title = str(item.get("title") or "").strip()
            raw_content = str(item.get("content") or "").strip()
            url = str(item.get("url") or "").strip()
            normalized_url = _normalize_url(url)
            denied_marker = any(
                marker in f"{raw_title} {raw_content}".lower()
                for marker in ("dianping.com", "大众点评")
            )
            if not normalized_url:
                _log_activity("search", "过滤无来源链接的网页结果", raw_title or "未命名结果")
                continue
            if _is_denied_source(normalized_url) or denied_marker:
                _log_activity("search", "过滤受限网页来源", normalized_url)
                continue
            if normalized_url and normalized_url in seen_urls:
                continue
            if normalized_url:
                seen_urls.add(normalized_url)
            safe_item = dict(item)
            safe_item["url"] = normalized_url or url
            merged_results.append(safe_item)

    if not merged_results and market_snapshot:
        return market_snapshot
    if not merged_results:
        _log_activity("tool", "web_search 完成", "未找到结果")
        return "没有找到可用的联网搜索结果。"

    processed = []
    for index, item in enumerate(merged_results, start=1):
        title = (item.get("title") or "未命名结果").strip()
        url = (item.get("url") or "").strip()
        summary = str(item.get("content") or "").strip().replace("\n", " ")
        found_dates = _extract_dates(f"{title} {summary}")
        latest_date = max(found_dates) if found_dates else None
        processed.append(
            {
                "rank": index,
                "title": title,
                "url": url,
                "summary": summary,
                "latest_date": latest_date,
            }
        )

    if time_sensitive:
        processed.sort(
            key=lambda item: (
                item["latest_date"] is not None,
                item["latest_date"].toordinal() if item["latest_date"] else -1,
                -item["rank"],
            ),
            reverse=True,
        )

    freshest_date = max((item["latest_date"] for item in processed if item["latest_date"]), default=None)
    staleness_warning = ""
    if time_sensitive and freshest_date:
        delta = (today - freshest_date).days
        if delta > 3:
            staleness_warning = (
                f"搜索结果中能识别出的最新日期是 {freshest_date.isoformat()}，"
                f"距离当前日期 {today.isoformat()} 已超过 {delta} 天。"
            )
    elif time_sensitive and not freshest_date:
        staleness_warning = (
            f"搜索结果里没有识别到明确日期，无法确认是否与当前日期 {today.isoformat()} 同步。"
        )

    if market_sensitive and staleness_warning:
        staleness_warning += " 对于实时行情或指数类问题，不应把这些结果当作今日实时数据。"

    lines = [f"搜索关键词: {query}"]
    if time_sensitive:
        lines.append(f"当前日期: {today.isoformat()}")
    if weather_result and "失败" not in weather_result:
        lines.extend(["", weather_result])
        _log_activity("tool", "优先使用天气接口", "网页搜索结果仅作为补充来源")
    if hs_snapshot:
        lines.extend(["", hs_snapshot])
        _log_activity("tool", "优先使用沪深股票接口", "网页搜索结果仅作为补充来源")
    if market_snapshot:
        lines.extend(["", market_snapshot])
        _log_activity("tool", "优先使用行情快照", "网页搜索结果仅作为补充来源")
    if staleness_warning:
        lines.extend(["", f"时效警告: {staleness_warning}"])
        _log_activity("search", "识别到时效风险", staleness_warning)

    lines.extend(["", "检索结果:"])
    for index, item in enumerate(processed[:6], start=1):
        date_label = item["latest_date"].isoformat() if item["latest_date"] else "未识别"
        lines.append(f"{index}. 标题: {item['title']}")
        lines.append(f"   日期线索: {date_label}")
        lines.append(f"   摘要: {item['summary'] or '无摘要'}")
        lines.append(f"   链接: {item['url'] or '无链接'}")
        if item["url"]:
            _log_source_card(item["title"], item["url"], item["summary"][:160], date_label)

    _log_activity("tool", "web_search 完成", f"返回 {min(len(processed), 6)} 条候选结果")
    return "\n".join(lines)


@tool
def web_search(query: str) -> str:
    """Search the public web for fresh or time-sensitive information when the user explicitly enabled web search."""
    return perform_web_search(query)


connection = sqlite3.connect(DB_PATH, check_same_thread=False)
checkpoint = SqliteSaver(connection)
checkpoint.setup()


def has_checkpoint_data(thread_id: str) -> bool:
    """Return whether a thread has legacy LangGraph state or writes.

    Anonymous access must not mint a first capability for an old checkpoint
    thread because the checkpoint store has no guest ownership metadata.  A
    storage read failure fails closed so it cannot become an authorization
    bypass.
    """

    try:
        row = connection.execute(
            """
            SELECT 1 FROM checkpoints WHERE thread_id = ?
            UNION ALL
            SELECT 1 FROM writes WHERE thread_id = ?
            LIMIT 1
            """,
            (thread_id, thread_id),
        ).fetchone()
    except Exception:
        return True
    return row is not None

BASE_SYSTEM_PROMPT = f"""
你是一个通用 AI 助手，面向多种办公、学习、创作、分析与问答场景。
当前日期是 {_today_cn().isoformat()}。

工作原则：
1. 默认直接回答，先理解用户要解决的问题。
2. 如果用户上传了文件，优先基于文件内容作答，并在可能时引用文件名、页码、sheet 或片段标签。
3. 如果文件内容存在截断、扫描件缺字或解析损失，要坦诚说明。
4. 如果用户上传了图片，先结合视觉内容进行 OCR、图表阅读、截图理解或图片分析。
5. 当需要结构化输出时，优先使用清晰的 Markdown。
6. 对代码、表格、方案、总结，尽量给出可直接使用的结果。
7. 只有在用户明确开启联网搜索，且问题需要最新外部信息时，才调用 web_search。
8. 对于指数/股价/行情这类问题，优先使用工具返回的行情快照；如果快照不可用，再参考网页搜索。
9. 如果搜索结果出现时效警告、旧日期或无法识别日期，必须明确告诉用户结果可能不是今天/当前的数据。
10. 不要暴露内部私有推理，只输出结论、必要依据和工具结果。
"""

SEARCH_DISABLED_APPENDIX = """
当前这轮对话未开启联网搜索。即使你知道有 web_search 工具，也不要调用。
"""

SEARCH_ENABLED_APPENDIX = """
当前这轮对话已开启联网搜索。
如果用户在问最新、今天、实时、当前值，必须优先参考工具返回中的日期线索，过滤过旧结果。
"""

agent_without_search = create_agent(
    model=model,
    tools=[current_datetime, weather_lookup],
    system_prompt=BASE_SYSTEM_PROMPT + SEARCH_DISABLED_APPENDIX,
    checkpointer=checkpoint,
)

agent_with_search = create_agent(
    model=model,
    tools=[current_datetime, weather_lookup, web_search],
    system_prompt=BASE_SYSTEM_PROMPT + SEARCH_ENABLED_APPENDIX,
    checkpointer=checkpoint,
)


def _build_attachment_block(attachments: list[dict], query: str) -> str:
    if not attachments:
        return ""

    blocks = []
    for index, attachment in enumerate(attachments, start=1):
        header = [
            f"[文件{index}]",
            f"名称: {attachment['name']}",
            f"类型: {attachment['extension']}",
            f"大小: {attachment['size_bytes']} bytes",
        ]
        if attachment.get("note"):
            header.append(f"说明: {attachment['note']}")

        if attachment.get("modality") == "image":
            storage_label = "OSS URL" if attachment.get("storage") == "oss" else "内联图片"
            header.append(f"处理方式: 图片会直接发送给多模态模型。图片来源: {storage_label}")
            blocks.append("\n".join(header))
            continue

        header.append(f"切分片段数: {attachment.get('chunk_count', 0)}")
        if attachment.get("preview"):
            header.append("文件摘要预览:")
            header.append(attachment["preview"])

        relevant_chunks = _select_relevant_chunks(query, attachment.get("chunks", []), attachment=attachment)
        if relevant_chunks:
            header.append("与当前问题最相关的片段:")
            for chunk in relevant_chunks:
                header.append(chunk["label"])
                header.append(chunk["text"])
        elif attachment.get("content"):
            header.append("提取内容:")
            header.append(attachment["content"])
        blocks.append("\n".join(header))

    return (
        f"\n\n{ATTACHMENT_START}\n"
        "以下是系统从用户上传文件中整理出的上下文，请优先基于这些内容回答：\n\n"
        + "\n\n".join(blocks)
        + f"\n{ATTACHMENT_END}"
    )


def _build_search_block(search_enabled: bool) -> str:
    state = "enabled" if search_enabled else "disabled"
    message = "联网搜索已开启。" if search_enabled else "联网搜索未开启。"
    return f"\n\n{SEARCH_START}\nstate: {state}\n{message}\n{SEARCH_END}"


def build_user_prompt(message: str, attachments: list[dict], search_enabled: bool) -> str:
    display_text = message.strip()
    if not display_text and attachments:
        display_text = "请结合我上传的文件或图片给出分析和回答。"
    if not display_text:
        display_text = "请继续。"
    return display_text + _build_attachment_block(attachments, display_text) + _build_search_block(search_enabled)


def _build_user_content(message: str, attachments: list[dict], search_enabled: bool):
    prompt_text = build_user_prompt(message, attachments, search_enabled)
    image_attachments = [
        attachment
        for attachment in attachments
        if attachment.get("modality") == "image" and attachment.get("image_url")
    ]

    if not image_attachments:
        return prompt_text

    content = [{"type": "text", "text": prompt_text}]
    for attachment in image_attachments:
        content.append({"type": "image_url", "image_url": {"url": attachment["image_url"]}})
    return content


def _encode_metadata(attachments: list[dict], search_enabled: bool) -> str:
    payload = {
        "attachments": [
            {
                "name": attachment["name"],
                "extension": attachment["extension"],
                "modality": attachment.get("modality", "text"),
                "image_url": attachment.get("image_url"),
                "storage": attachment.get("storage", ""),
                "object_key": attachment.get("object_key", ""),
            }
            for attachment in attachments
        ],
        "search_enabled": search_enabled,
    }
    return json.dumps(payload, ensure_ascii=False)


def _extract_text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "".join(parts)
    return str(content or "")


def _build_display_text(user_text: str, attachments: list[dict], search_enabled: bool) -> str:
    safe_text = user_text.strip() or "请结合我上传的文件或图片回答。"
    metadata = _encode_metadata(attachments, search_enabled)
    return f"{safe_text}\n\n{ACTIVITY_START}{metadata}{ACTIVITY_END}"


def _build_current_travel_plan_block(plan: TravelPlan) -> str:
    day_lines = []
    for day in plan.days[:7]:
        titles = [item.title for item in day.items[:6]]
        day_lines.append(f"{day.date}: {'、'.join(titles) if titles else '暂无安排'}")
    return (
        f"\n\n当前已保存旅行计划（仅作对话上下文，不是私有推理）："
        f"{plan.destination or '未定目的地'}，{plan.start_date} 至 {plan.end_date}，第 {plan.version} 版。\n"
        + "\n".join(day_lines)
        + "\n如用户提出旅行相关修改，应优先基于该计划重规划并保留已锁定项目。"
    )


def _message_mentions_travel_context(message: str) -> bool:
    return any(
        keyword in message
        for keyword in [
            "行程",
            "路线",
            "旅行",
            "旅游",
            "出行",
            "出发",
            "目的地",
            "景点",
            "酒店",
            "餐厅",
            "咖啡馆",
            "车票",
            "机票",
            "高铁",
            "航班",
            "交通",
            "周边",
            "下雨",
            "天气",
            "晚点",
        ]
    )


def _message_mentions_replan(message: str) -> bool:
    explicit_replan = ["重新规划", "重排", "改行程", "改路线", "调整行程", "调整路线"]
    if any(keyword in message for keyword in explicit_replan):
        return True
    return _message_mentions_travel_context(message) and any(
        keyword in message
        for keyword in ["下雨", "晚点", "不想走路", "少走路", "改成", "增加", "去掉", "取消", "预算变"]
    )


def _is_travel_query(message: str, attachments: list[dict]) -> bool:
    travel_keywords = [
        "\u65c5\u884c",
        "\u51fa\u884c",
        "\u884c\u7a0b",
        "\u9ad8\u94c1",
        "\u706b\u8f66",
        "12306",
        "\u673a\u7968",
        "\u98de\u673a",
        "\u822a\u73ed",
        "\u9152\u5e97",
        "\u666f\u70b9",
        "\u5468\u8fb9",
        "\u653b\u7565",
        "\u8def\u7ebf",
        "\u9910\u5385",
        "\u5496\u5561\u9986",
        "\u7f51\u7ea2",
        "\u70ed\u95e8",
        "\u6253\u5361",
        "\u4e0b\u96e8",
        "\u665a\u70b9",
        "\u4e0d\u60f3\u8d70\u8def",
        "\u5c11\u8d70\u8def",
        "\u91cd\u65b0\u89c4\u5212",
    ]
    if any(keyword in message for keyword in travel_keywords):
        return True

    if not attachments:
        return False

    lowered = message.lower()
    attachment_names = " ".join(attachment.get("name", "") for attachment in attachments).lower()
    return any(token in lowered or token in attachment_names for token in ["trip", "travel", "flight", "hotel", "ticket"])


def _stream_travel_response(
    message: str,
    thread_id: str,
    search_enabled: bool,
    attachments: list[dict],
    user_id: int | None = None,
):
    _log_activity("think", "Travel intent detected", "Route request to travel orchestrator")
    _log_activity("tool", "Build travel context", "Extract origin, destination, date, preferences, attachments", state="running")
    current_plan = get_current_plan(thread_id, user_id=user_id) if thread_id else None
    response = plan_travel(
        message,
        attachments,
        thread_id=thread_id,
        search_enabled=search_enabled,
        current_plan=current_plan,
        activity_logger=_log_activity,
    )
    _log_activity("tool", "Build travel context", "Travel context ready")

    if response.transport_options:
        _log_activity("tool", "Build transport candidates", f"candidate count {len(response.transport_options)}")
    if response.timeline:
        _log_activity("tool", "Build itinerary timeline", f"timeline items {len(response.timeline)}")
    if response.poi_recommendations:
        _log_activity("tool", "Recommend nearby places", f"poi count {len(response.poi_recommendations)}")
    if response.weather_summary:
        _log_activity("tool", "Attach weather summary", "weather context added")

    saved_plan = response.trip_plan
    if saved_plan is not None and thread_id:
        try:
            saved_plan = save_plan_version(
                saved_plan,
                user_id=user_id,
                change_summary="重规划" if current_plan else "首次生成",
                # Version 0 is the CAS token for the first write.  Passing
                # None here would make concurrent first requests both look
                # like valid creates.
                expected_version=current_plan.version if current_plan else 0,
            )
        except Exception as exc:
            _log_activity("storage", "旅行计划保存失败", str(exc))
            response.alerts.append("本次计划尚未成功持久化，请刷新当前计划后再继续修改。")
            saved_plan = None
            response.trip_plan = None
        else:
            response.trip_plan = saved_plan
            response.sources = saved_plan.sources
            response.conflicts = saved_plan.conflicts
            _travel_plan_var.set(asdict(saved_plan))
            for evidence in saved_plan.sources:
                if evidence.url:
                    _log_source_card(
                        title=evidence.title,
                        url=evidence.url,
                        summary=evidence.snippet,
                        source_date=evidence.retrieved_at,
                        evidence_id=evidence.evidence_id,
                    )
            try:
                save_travel_turn(
                    thread_id=thread_id,
                    user_id=user_id,
                    role="user",
                    content=message.strip() or "请结合附件生成旅行计划。",
                    attachments=[
                        {
                            "name": attachment.get("name", ""),
                            "extension": attachment.get("extension", ""),
                            "modality": attachment.get("modality", "text"),
                            "image_url": attachment.get("image_url"),
                        }
                        for attachment in attachments
                    ],
                    search_enabled=search_enabled,
                )
            except Exception as exc:
                _log_activity("storage", "旅行请求历史保存失败", str(exc))
    rendered = render_travel_response(response)
    if saved_plan is not None and thread_id:
        try:
            save_travel_turn(
                thread_id=thread_id,
                user_id=user_id,
                role="assistant",
                content=rendered,
                search_enabled=search_enabled,
                plan=saved_plan,
            )
        except Exception as exc:
            _log_activity("storage", "旅行回复历史保存失败", str(exc))

    yield AIMessageChunk(content=[{"type": "text", "text": rendered}]), {
        "travel": True,
        "trip_plan": asdict(saved_plan) if saved_plan is not None and thread_id else None,
    }


def stream_chat(
    message: str,
    thread_id: str,
    search_enabled: bool,
    attachments: list[dict],
    user_id: int | None = None,
):
    _reset_runtime_buffers()

    _log_activity("think", "分析用户问题", message.strip() or "结合上传内容回答", state="running")
    if attachments:
        image_count = sum(1 for attachment in attachments if attachment.get("modality") == "image")
        text_count = len(attachments) - image_count
        if text_count:
            _log_activity("attachment", "读取上传文件", f"{text_count} 个文档已进入上下文")
        if image_count:
            _log_activity("attachment", "附加图片内容", f"{image_count} 张图片将交给多模态模型")
    else:
        _log_activity("attachment", "未附加文件", "本轮仅处理文本问题")

    if search_enabled:
        _log_activity("search", "联网搜索已开启", "如需要最新信息，将自动执行时效校验。")
    else:
        _log_activity("search", "联网搜索未开启", "本轮回答不会访问外部网页。")

    _log_activity("think", "整理回答策略", "准备汇总上下文并生成最终回复")

    current_plan = get_current_plan(thread_id, user_id=user_id) if thread_id else None
    if _is_travel_query(message, attachments) or (
        current_plan and (_message_mentions_travel_context(message) or _message_mentions_replan(message))
    ):
        return _stream_travel_response(message, thread_id, search_enabled, attachments, user_id=user_id)

    prompt_text = build_user_prompt(message, attachments, search_enabled)
    if current_plan:
        current_plan_block = _build_current_travel_plan_block(current_plan)
        prompt_text += current_plan_block
    user_content = _build_user_content(message, attachments, search_enabled)
    if current_plan:
        if isinstance(user_content, str):
            user_content += current_plan_block
        else:
            user_content[0]["text"] += current_plan_block
    display_text = _build_display_text(message, attachments, search_enabled)
    visible_text = _strip_internal_sections(display_text)
    metadata_suffix = display_text[len(visible_text):] if display_text.startswith(visible_text) else ""

    if isinstance(user_content, str):
        content = f"{prompt_text}{metadata_suffix}"
    else:
        content = [{"type": "text", "text": f"{prompt_text}{metadata_suffix}"}]
        content.extend(user_content[1:])

    user_message = HumanMessage(content=content)
    config = {"configurable": {"thread_id": thread_id}}
    selected_agent = agent_with_search if search_enabled else agent_without_search
    return selected_agent.stream({"messages": [user_message]}, config, stream_mode="messages")


def _strip_internal_sections(text: str) -> str:
    stripped = re.sub(rf"\s*{re.escape(ACTIVITY_START)}[\s\S]*?{re.escape(ACTIVITY_END)}", "", text)
    stripped = re.sub(rf"\s*{re.escape(ATTACHMENT_START)}[\s\S]*?{re.escape(ATTACHMENT_END)}", "", stripped)
    stripped = re.sub(rf"\s*{re.escape(SEARCH_START)}[\s\S]*?{re.escape(SEARCH_END)}", "", stripped)
    return stripped.strip()


def _extract_metadata(text: str) -> dict:
    match = re.search(rf"{re.escape(ACTIVITY_START)}([\s\S]*?){re.escape(ACTIVITY_END)}", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def encode_assistant_metadata(activities: list[dict], sources: list[dict]) -> str:
    payload = {
        "activities": activities or [],
        "sources": sources or [],
    }
    return f"\n\n{ASSISTANT_META_START}{json.dumps(payload, ensure_ascii=False)}{ASSISTANT_META_END}"


def _strip_assistant_metadata(text: str) -> str:
    return re.sub(rf"\s*{re.escape(ASSISTANT_META_START)}[\s\S]*?{re.escape(ASSISTANT_META_END)}", "", text).strip()


def _extract_assistant_metadata(text: str) -> dict:
    match = re.search(rf"{re.escape(ASSISTANT_META_START)}([\s\S]*?){re.escape(ASSISTANT_META_END)}", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def _travel_turn_messages(thread_id: str, user_id: int | None = None) -> list[dict]:
    """Expose legacy travel turns through the chat history shape."""
    return _stored_turn_messages(list_travel_turns(thread_id, user_id=user_id))


def _stored_turn_messages(turns: list[dict]) -> list[dict]:
    """Convert durable conversation turns to the frontend history shape."""
    result = []
    for turn in turns:
        attachments = turn.get("attachments") or []
        if turn.get("role") == "user":
            result.append(
                {
                    "role": "user",
                    "content": str(turn.get("content") or ""),
                    "attachments": attachments,
                    "search_enabled": bool(turn.get("search_enabled")),
                    "image_urls": [item.get("image_url") for item in attachments if item.get("image_url")],
                }
            )
        elif turn.get("role") == "assistant":
            content = str(turn.get("content") or "")
            metadata = _extract_assistant_metadata(content)
            cleaned_content = _strip_assistant_metadata(content)
            if not cleaned_content:
                continue
            result.append(
                {
                    "role": "assistant",
                    "content": cleaned_content,
                    "activities": metadata.get("activities", []),
                    "sources": metadata.get("sources", []),
                    "plan_id": turn.get("plan_id"),
                    "plan_version": turn.get("plan_version"),
                }
            )
    return result


def _conversation_turn_messages(thread_id: str, user_id: int | None = None) -> list[dict]:
    return _stored_turn_messages(list_conversation_turns(thread_id, user_id=user_id))


def _checkpoint_message_item(msg) -> dict | None:
    content = _extract_text_content(msg.content)
    if not content:
        return None
    if isinstance(msg, HumanMessage):
        metadata = _extract_metadata(content)
        attachments = metadata.get("attachments", [])
        return {
            "role": "user",
            "content": _strip_internal_sections(content),
            "attachments": attachments,
            "search_enabled": bool(metadata.get("search_enabled")),
            "image_urls": [item.get("image_url") for item in attachments if item.get("image_url")],
        }
    if isinstance(msg, AIMessage):
        metadata = _extract_assistant_metadata(content)
        cleaned_content = _strip_assistant_metadata(content)
        if not cleaned_content:
            return None
        return {
            "role": "assistant",
            "content": cleaned_content,
            "activities": metadata.get("activities", []),
            "sources": metadata.get("sources", []),
        }
    return None


def _history_datetime(value: object) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.replace(tzinfo=parsed.tzinfo or CN_TZ)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=CN_TZ)


def _history_timestamp_is_known(value: datetime) -> bool:
    return value != datetime.min.replace(tzinfo=CN_TZ)


def _legacy_checkpoint_events(thread_id: str) -> list[tuple[datetime, int, dict]]:
    """Recover per-checkpoint timestamps for histories written before turn log."""

    try:
        snapshots = list(checkpoint.list({"configurable": {"thread_id": thread_id}}))
    except Exception:
        return []

    events: list[tuple[datetime, int, dict]] = []
    seen_ids: set[str] = set()
    seen_counts: dict[tuple[str, str], int] = {}
    sequence = 0
    for snapshot in reversed(snapshots):
        state = snapshot.checkpoint if isinstance(snapshot.checkpoint, dict) else {}
        values = state.get("channel_values") or {}
        messages = values.get("messages") or []
        occurrence_counts: dict[tuple[str, str], int] = {}
        timestamp = state.get("ts") or (snapshot.metadata or {}).get("created_at", "")
        for msg in messages:
            message_id = str(getattr(msg, "id", "") or "").strip()
            if message_id:
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
            else:
                base = (msg.__class__.__name__, _extract_text_content(getattr(msg, "content", "")))
                occurrence = occurrence_counts.get(base, 0)
                occurrence_counts[base] = occurrence + 1
                if occurrence < seen_counts.get(base, 0):
                    continue
                seen_counts[base] = occurrence + 1

            item = _checkpoint_message_item(msg)
            if item is None:
                continue
            events.append((_history_datetime(timestamp), sequence, item))
            sequence += 1
    return events


def _merge_legacy_history(
    thread_id: str,
    user_id: int | None,
    turns: list[dict] | None = None,
) -> list[dict]:
    checkpoint_events = _legacy_checkpoint_events(thread_id)
    turns = turns if turns is not None else list_conversation_turns(thread_id, user_id=user_id)
    events: list[tuple[datetime, int, dict, str]] = [
        (timestamp, sequence, item, "checkpoint")
        for timestamp, sequence, item in checkpoint_events
    ]
    for index, turn in enumerate(turns, start=len(events)):
        stored = _stored_turn_messages([turn])
        if stored:
            events.append((_history_datetime(turn.get("created_at")), index, stored[0], "turn"))

    # A current ordinary-chat checkpoint often contains the same user/assistant
    # pair that was also written to the ordered turn log.  Match those copies
    # one-to-one and only inside a small timestamp window; content alone is
    # not a safe identity because users can legitimately repeat a question.
    checkpoint_events = [event for event in events if event[3] == "checkpoint"]
    turn_events = [event for event in events if event[3] == "turn"]
    matched_checkpoint_sequences: set[int] = set()
    used_checkpoint_sequences: set[int] = set()
    for turn_timestamp, _turn_sequence, turn_item, _source in turn_events:
        if not _history_timestamp_is_known(turn_timestamp):
            continue
        candidates = [
            event
            for event in checkpoint_events
            if event[1] not in used_checkpoint_sequences
            and event[2].get("role") == turn_item.get("role")
            and event[2].get("content") == turn_item.get("content")
            and _history_timestamp_is_known(event[0])
            and abs((event[0] - turn_timestamp).total_seconds()) <= 5 * 60
        ]
        if not candidates:
            continue
        matched = min(candidates, key=lambda event: abs((event[0] - turn_timestamp).total_seconds()))
        used_checkpoint_sequences.add(matched[1])
        matched_checkpoint_sequences.add(matched[1])
    events.sort(key=lambda item: (item[0], item[1]))

    result: list[dict] = []
    for _timestamp, _sequence, item, source in events:
        if source == "checkpoint" and _sequence in matched_checkpoint_sequences:
            continue
        if item.get("role") == "assistant" and result and result[-1].get("role") == "assistant":
            if item.get("content") not in result[-1].get("content", ""):
                result[-1]["content"] = f"{result[-1]['content']}\n\n{item['content']}".strip()
            result[-1].setdefault("activities", []).extend(item.get("activities", []))
            result[-1].setdefault("sources", []).extend(item.get("sources", []))
        else:
            result.append(item)
    return result


def get_messages(thread_id: str, user_id: int | None = None) -> list[dict]:
    conversation_turns = list_conversation_turns(thread_id, user_id=user_id)
    cp = checkpoint.get({"configurable": {"thread_id": thread_id}})
    if not cp:
        return _conversation_turn_messages(thread_id, user_id=user_id)

    channel_values = cp.get("channel_values")
    if not channel_values:
        return _conversation_turn_messages(thread_id, user_id=user_id)

    messages = channel_values.get("messages", [])
    if not messages:
        return _conversation_turn_messages(thread_id, user_id=user_id)

    # Once ordinary chat turns are recorded, the durable turn log is the
    # source of truth for ordering travel and non-travel messages.  The
    # checkpoint remains the fallback for histories written before this log
    # existed.
    if conversation_turns:
        merged_legacy = _merge_legacy_history(thread_id, user_id, conversation_turns)
        if merged_legacy:
            return merged_legacy

    result = []
    for msg in messages:
        content = _extract_text_content(msg.content)
        if not content:
            continue

        if isinstance(msg, HumanMessage):
            metadata = _extract_metadata(content)
            attachments = metadata.get("attachments", [])
            result.append(
                {
                    "role": "user",
                    "content": _strip_internal_sections(content),
                    "attachments": attachments,
                    "search_enabled": bool(metadata.get("search_enabled")),
                    "image_urls": [item.get("image_url") for item in attachments if item.get("image_url")],
                }
            )
        elif isinstance(msg, AIMessage):
            metadata = _extract_assistant_metadata(content)
            cleaned_content = _strip_assistant_metadata(content)
            if not cleaned_content:
                continue
            if result and result[-1].get("role") == "assistant":
                if cleaned_content not in result[-1]["content"]:
                    result[-1]["content"] = f"{result[-1]['content']}\n\n{cleaned_content}".strip()
                result[-1]["activities"].extend(metadata.get("activities", []))
                result[-1]["sources"].extend(metadata.get("sources", []))
                continue
            result.append(
                {
                    "role": "assistant",
                    "content": cleaned_content,
                    "activities": metadata.get("activities", []),
                    "sources": metadata.get("sources", []),
                }
            )
    travel_messages = _travel_turn_messages(thread_id, user_id=user_id)
    if travel_messages:
        # A travel turn is persisted outside the LangGraph execution state.
        # Avoid duplicating it when a future checkpoint implementation starts
        # replaying the same turn, while keeping legacy/general messages intact.
        existing_contents = {(item.get("role"), item.get("content")) for item in result}
        for item in travel_messages:
            if (item.get("role"), item.get("content")) not in existing_contents:
                result.append(item)
    return result


def attach_assistant_metadata(thread_id: str, assistant_text: str, activities: list[dict], sources: list[dict]) -> None:
    if not assistant_text:
        return

    config = {"configurable": {"thread_id": thread_id}}
    cp = checkpoint.get(config)
    if not cp:
        return

    channel_values = cp.get("channel_values") or {}
    messages = channel_values.get("messages") or []
    if not messages:
        return

    metadata = encode_assistant_metadata(activities, sources)
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = _extract_text_content(msg.content)
        if not content or ASSISTANT_META_START in content:
            continue
        if assistant_text.strip() and assistant_text.strip() not in content:
            continue
        if isinstance(msg.content, str):
            msg.content = f"{msg.content}{metadata}"
        elif isinstance(msg.content, list):
            for item in msg.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    item["text"] = f"{item.get('text', '')}{metadata}"
                    break
        checkpoint.put(config, cp["checkpoint"], cp.get("metadata", {}), cp.get("new_versions", {}))
        return


def derive_session_title(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", "")).replace("\n", " ").strip()
        if content:
            return content[:16]

        attachments = message.get("attachments") or []
        if attachments:
            return f"文件问答: {attachments[0]['name'][:8]}"
    return DEFAULT_THREAD_TITLE


def list_threads() -> list[dict]:
    rows = connection.execute(
        """
        SELECT thread_id, MAX(sort_rowid) AS latest_rowid
        FROM (
            SELECT thread_id, rowid AS sort_rowid FROM checkpoints
            UNION ALL
            SELECT thread_id, rowid AS sort_rowid FROM writes
        )
        GROUP BY thread_id
        ORDER BY latest_rowid DESC
        """
    ).fetchall()

    sessions = []
    for thread_id, _latest_rowid in rows:
        try:
            messages = get_messages(thread_id)
        except Exception:
            messages = []
        title = derive_session_title(messages) if messages else thread_id
        sessions.append({"thread_id": thread_id, "title": title})
    return sessions


def delete_thread(thread_id: str, user_id: int | None = None):
    for message in get_messages(thread_id, user_id=user_id):
        for attachment in message.get("attachments", []):
            if attachment.get("storage") == "oss" and attachment.get("object_key"):
                delete_oss_object(attachment["object_key"])
    checkpoint.delete_thread(thread_id)
    delete_travel_thread(thread_id, user_id=user_id)
