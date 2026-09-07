"""Opt-in web discovery for travel places.

Web search is deliberately a discovery layer only.  A search result is not
treated as a rating, opening-hours fact, or verified place until a map/POI
adapter resolves it.  The module is dependency-light so tests can inject a
fake searcher without touching the network.
"""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

try:
    from langchain_tavily import TavilySearch
except Exception:  # pragma: no cover - optional dependency guard
    TavilySearch = None


CN_TZ = ZoneInfo("Asia/Shanghai")
DENIED_SOURCE_HOSTS = {"dianping.com", "www.dianping.com", "m.dianping.com"}


@dataclass(slots=True)
class TravelSearchResult:
    candidates: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempted: bool = False
    status: str = "not_requested"


def _now_label() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _normalize_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            "",
        )
    )


def _is_denied_source(url: str) -> bool:
    parsed = urlsplit(url if "://" in url else f"//{url}")
    host = parsed.hostname or ""
    host = host.lower().rstrip(".")
    return host in DENIED_SOURCE_HOSTS or host.endswith(".dianping.com")


def web_evidence_id(*, url: str, title: str, anchor: str, query: str) -> str:
    key = _normalize_url(url) or "|".join((anchor, query, title))
    digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"web_{digest}"


# Backwards-compatible internal name for callers written before the public
# source-ID seam was introduced.
_evidence_id = web_evidence_id


def _default_searcher():
    if TavilySearch is None or not os.getenv("TAVILY_API_KEY"):
        return None
    return TavilySearch(
        max_results=5,
        topic="general",
        include_images=False,
        include_answer=False,
        include_raw_content=False,
        search_depth="advanced",
        handle_tool_error=True,
        handle_validation_error="搜索参数无效，请简化关键词后重试。",
    )


def discover_travel_places(
    city: str,
    *,
    anchor: str = "",
    preferences: list[str] | None = None,
    search_enabled: bool,
    searcher=None,
    activity_logger=None,
) -> TravelSearchResult:
    """Discover candidate places only when the user explicitly enabled search."""

    result = TravelSearchResult()
    if not search_enabled:
        result.status = "disabled"
        return result

    result.attempted = True
    if not city:
        result.errors.append("缺少目的地，无法进行附近地点搜索。")
        result.status = "failed"
        return result

    active_searcher = searcher or _default_searcher()
    if active_searcher is None:
        result.errors.append("未配置 Tavily 搜索能力。")
        result.status = "not_configured"
        if activity_logger:
            activity_logger("search", "旅行网页搜索不可用", "未配置 Tavily API Key")
        return result

    preference_text = "、".join((preferences or [])[:4])
    anchor_text = f"，靠近{anchor}" if anchor else ""
    queries = [
        f"{city}{anchor_text} 附近 好吃 餐厅 咖啡馆 推荐 评分",
        f"{city}{anchor_text} 附近 热门景点 网红 打卡 好玩 近期推荐",
    ]
    if preference_text:
        queries = [f"{query} 偏好：{preference_text}" for query in queries]

    seen_urls: set[str] = set()
    retrieved_at = _now_label()
    for query in queries:
        if activity_logger:
            activity_logger("search", "发现旅行地点", query, "running")
        try:
            raw = active_searcher.invoke({"query": query})
        except Exception as exc:
            result.errors.append(f"旅行网页搜索失败：{type(exc).__name__}")
            result.status = "partial" if result.candidates else "failed"
            if activity_logger:
                activity_logger("tool", "旅行网页搜索失败", type(exc).__name__)
            continue

        if not isinstance(raw, dict) or "results" not in raw or not isinstance(raw["results"], list):
            result.errors.append(f"{query}: 搜索服务返回格式无效。")
            result.status = "partial" if result.candidates else "failed"
            continue
        items = raw["results"]
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            raw_content = str(item.get("content") or "").strip()
            normalized_url = _normalize_url(url)
            if not title or not normalized_url:
                continue
            if _is_denied_source(normalized_url) or any(
                marker in f"{title} {url} {raw_content}".lower()
                for marker in ("dianping.com", "大众点评")
            ):
                # Travel discovery must not copy or expose review text from
                # Dianping.  Such a result is not a candidate source.
                continue
            if normalized_url and normalized_url in seen_urls:
                continue
            if normalized_url:
                seen_urls.add(normalized_url)
            evidence_id = web_evidence_id(url=normalized_url, title=title, anchor=anchor, query=query)
            result.sources.append(
                {
                    "evidence_id": evidence_id,
                    "source_type": "web_search",
                    "provider": "Tavily",
                    "title": title,
                    "url": normalized_url,
                    # Search content is discovery text, not an authorized
                    # review excerpt.  Keep only metadata in the durable
                    # evidence record.
                    "snippet": "",
                    "retrieved_at": retrieved_at,
                    "valid_until": "",
                    "freshness": "unknown",
                    "reliability": "discovery_only",
                    "supports": ["nearby_discovery", "popularity_signal"],
                    "is_demo": False,
                }
            )
            result.candidates.append(
                {
                    "name_hint": title,
                    "category_hint": "美食" if "餐" in query or "吃" in query else "景点",
                    "url": normalized_url,
                    "snippet": "",
                    "source_id": evidence_id,
                    "rank": index,
                }
            )
        if activity_logger:
            activity_logger(
                "result",
                "搜索资料已返回",
                f"本次返回 {len(items)} 条，保留 {len(result.sources)} 条可追溯来源",
            )

    if result.candidates:
        result.status = "partial" if result.errors else "success"
    elif result.errors:
        result.status = result.status if result.status in {"failed", "not_configured"} else "failed"
    else:
        result.status = "empty"
    return result
