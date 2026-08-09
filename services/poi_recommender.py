from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from adapters.amap_adapter import resolve_place_in_city, search_pois, search_pois_around_location
from agents.schemas import Evidence, PoiGroup, PoiRecommendation, TravelQuery
from services.travel_search import discover_travel_places


CN_TZ = ZoneInfo("Asia/Shanghai")
FOOD_TYPES = "050000|060000"
LEISURE_TYPES = "110000|120000|160000"


def _now_label() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _source_id(item: dict, provider: str = "amap") -> str:
    raw = "|".join(
        [
            provider,
            str(item.get("name", "")),
            str(item.get("address", "")),
            str(item.get("location", "")),
        ]
    )
    return f"{provider}_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:12]}"


def _poi_evidence(item: dict, category: str) -> Evidence:
    evidence_id = _source_id(item)
    supports = ["poi", "distance"]
    if item.get("rating"):
        supports.append("rating")
    if item.get("cost"):
        supports.append("cost")
    return Evidence(
        evidence_id=evidence_id,
        source_type="map_poi",
        provider="Amap",
        title=str(item.get("name") or "高德地点"),
        url="https://www.amap.com/",
        snippet=str(item.get("address") or ""),
        retrieved_at=_now_label(),
        freshness="current_query",
        reliability="primary_adapter",
        supports=supports,
    )


def _to_poi(
    item: dict,
    category: str,
    area: str,
    prefix: str = "",
    *,
    extra_source_ids: list[str] | None = None,
    popularity_signal: str = "",
    popularity_source: str = "",
    website_url: str = "",
) -> tuple[PoiRecommendation, Evidence]:
    source = _poi_evidence(item, category)
    summary_parts = []
    if item.get("rating"):
        summary_parts.append(f"评分 {item['rating']}（高德）")
    if item.get("cost"):
        summary_parts.append(f"人均 {item['cost']}")
    if item.get("address"):
        summary_parts.append(item["address"])
    if item.get("distance"):
        summary_parts.append(f"距离 {item['distance']} 米")

    summary = " | ".join(summary_parts) if summary_parts else "已由高德地点服务解析"
    if prefix:
        summary = f"{prefix} | {summary}"
    source_ids = [source.evidence_id]
    source_ids.extend(item.get("source_ids") or [])
    source_ids.extend(extra_source_ids or [])

    return (
        PoiRecommendation(
            name=item.get("name", ""),
            category=category,
            area=item.get("area", area),
            address=item.get("address", ""),
            distance=str(item.get("distance", "")),
            summary=summary,
            rating=str(item.get("rating", "")),
            rating_source="Amap" if item.get("rating") else "",
            estimated_cost=str(item.get("cost", "")),
            popularity_signal=popularity_signal,
            popularity_source=popularity_source,
            opening_status=str(item.get("opening_status") or "unknown"),
            source_ids=source_ids,
            website_url=website_url,
            freshness="current_query",
        ),
        source,
    )


def _sort_by_travel_fit(items: list[dict], preferences: list[str] | None = None) -> list[dict]:
    preference_text = " ".join(preferences or [])

    def rating_value(item: dict) -> float:
        try:
            return float(str(item.get("rating") or "0").strip())
        except ValueError:
            return 0.0

    def distance_value(item: dict) -> int:
        try:
            return int(item.get("distance") or 10**9)
        except (TypeError, ValueError):
            return 10**9

    def preference_bonus(item: dict) -> int:
        text = f"{item.get('name', '')} {item.get('category', '')}"
        return 1 if any(token and token in text for token in ["美食", "咖啡", "亲子"] if token in preference_text) else 0

    return sorted(
        items,
        key=lambda item: (-preference_bonus(item), -rating_value(item), distance_value(item)),
    )


def _dedupe_items(items: list[PoiRecommendation]) -> list[PoiRecommendation]:
    deduped: list[PoiRecommendation] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.name.strip(), item.address.strip())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_poi_groups(
    query: TravelQuery,
    *,
    search_enabled: bool = False,
    activity_logger=None,
) -> tuple[list[PoiGroup], list[Evidence], list[str]]:
    city = query.destination or query.city
    if not city:
        return [], [], ["缺少目的地，无法发现附近地点。"]

    groups: list[PoiGroup] = []
    evidence: list[Evidence] = []
    errors: list[str] = []
    anchors = query.named_places[:4] or [""]
    for place in anchors:
        resolved = resolve_place_in_city(city, place) if place else None
        anchor_name = resolved.get("name", place) if resolved else (place or city)

        food_items: list[dict] = []
        leisure_items: list[dict] = []
        if resolved and resolved.get("location"):
            food_items = search_pois_around_location(
                resolved["location"], page_size=4, radius=1800, types=FOOD_TYPES
            )
            leisure_items = search_pois_around_location(
                resolved["location"], page_size=3, radius=2200, types=LEISURE_TYPES
            )
        if not food_items:
            food_items = search_pois(anchor_name, "附近餐厅 美食 咖啡馆", page_size=4)
        if not leisure_items:
            leisure_items = search_pois(anchor_name, "附近景点 娱乐", page_size=3)

        items: list[PoiRecommendation] = []
        for item in _sort_by_travel_fit(food_items, query.preferences)[:3]:
            poi, source = _to_poi(item, "附近餐饮", city, prefix=f"围绕 {anchor_name} 推荐")
            items.append(poi)
            evidence.append(source)
        for item in _sort_by_travel_fit(leisure_items, query.preferences)[:2]:
            poi, source = _to_poi(item, "附近去处", city, prefix=f"围绕 {anchor_name} 推荐")
            items.append(poi)
            evidence.append(source)

        # Search can discover popularity signals, but map data remains the
        # gate for a place card.  Unresolved web titles stay in evidence only.
        search_result = discover_travel_places(
            city,
            anchor=anchor_name if place else "",
            preferences=query.preferences,
            search_enabled=search_enabled,
            activity_logger=activity_logger,
        )
        evidence.extend(Evidence(**item) for item in search_result.sources)
        errors.extend(search_result.errors)
        for candidate in search_result.candidates[:4]:
            resolved_candidate = resolve_place_in_city(city, candidate.get("name_hint", ""))
            if not resolved_candidate:
                errors.append(f"网页候选未通过地图地点验证：{candidate.get('name_hint', '')}")
                continue
            item = {
                **resolved_candidate,
                "source_ids": [candidate.get("source_id", "")],
            }
            poi, source = _to_poi(
                item,
                candidate.get("category_hint", "景点"),
                city,
                prefix="网页发现 + 地图验证",
                extra_source_ids=[candidate.get("source_id", "")],
                popularity_signal="网页结果提及（不等同于评分）",
                popularity_source=candidate.get("source_id", ""),
                website_url=candidate.get("url", ""),
            )
            items.append(poi)
            evidence.append(source)

        if items:
            groups.append(PoiGroup(anchor=anchor_name, items=_dedupe_items(items)[:8]))

    return groups, _dedupe_evidence(evidence), _dedupe_errors(errors)


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    result: list[Evidence] = []
    seen: set[str] = set()
    for item in items:
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        result.append(item)
    return result


def _dedupe_errors(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result[:8]


def recommend_pois(
    query: TravelQuery,
    *,
    search_enabled: bool = False,
    activity_logger=None,
) -> tuple[list[PoiRecommendation], list[PoiGroup], list[Evidence], list[str]]:
    city = query.destination or query.city
    if not city:
        return [], [], [], ["缺少目的地，无法推荐 POI。"]

    poi_groups, evidence, errors = build_poi_groups(
        query,
        search_enabled=search_enabled,
        activity_logger=activity_logger,
    )
    recommendations: list[PoiRecommendation] = []
    for group in poi_groups:
        recommendations.extend(group.items)

    # These are map-backed city-level candidates and work even when web search
    # is disabled.  Ratings are shown only if the adapter actually returned one.
    food_items = search_pois(city, "本地特色餐厅 美食", page_size=4)
    sight_items = search_pois(city, "热门景点 旅游", page_size=4)
    for item in _sort_by_travel_fit(food_items, query.preferences):
        poi, source = _to_poi(item, "美食", city)
        recommendations.append(poi)
        evidence.append(source)
    for item in _sort_by_travel_fit(sight_items, query.preferences):
        poi, source = _to_poi(item, "景点", city)
        recommendations.append(poi)
        evidence.append(source)

    deduped = _dedupe_items(recommendations)
    if not deduped:
        errors.append("当前未获取到可验证的地图 POI；未生成虚构地点占位卡。")
    return deduped[:16], poi_groups, _dedupe_evidence(evidence), _dedupe_errors(errors)
