"""Opt-in web discovery for travel places.

Web search is deliberately a discovery layer only.  A search result is not
treated as a rating, opening-hours fact, or verified place until a map/POI
adapter resolves it.  The module is dependency-light so tests can inject a
fake searcher without touching the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from langchain_tavily import TavilySearch
except Exception:  # pragma: no cover - optional dependency guard
    TavilySearch = None


CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True)
class TravelSearchResult:
    candidates: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempted: bool = False


def _now_label() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


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
        return result

    result.attempted = True
    if not city:
        result.errors.append("缺少目的地，无法进行附近地点搜索。")
        return result

    active_searcher = searcher or _default_searcher()
    if active_searcher is None:
        result.errors.append("未配置 Tavily 搜索能力。")
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
            message = f"{query}: {exc}"
            result.errors.append(message)
            if activity_logger:
                activity_logger("tool", "旅行网页搜索失败", str(exc))
            continue

        items = raw.get("results", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or item.get("snippet") or "").strip().replace("\n", " ")
            if not title and not url:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            evidence_id = f"web_{len(result.sources) + 1:03d}"
            result.sources.append(
                {
                    "evidence_id": evidence_id,
                    "source_type": "web_search",
                    "provider": "Tavily",
                    "title": title,
                    "url": url,
                    "snippet": snippet[:500],
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
                    "url": url,
                    "snippet": snippet[:500],
                    "source_id": evidence_id,
                    "rank": index,
                }
            )
        if activity_logger:
            activity_logger("search", "旅行网页搜索完成", f"候选 {len(result.candidates)} 条")

    return result
