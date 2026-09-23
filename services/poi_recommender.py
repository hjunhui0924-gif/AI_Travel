from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from adapters.amap_adapter import (
    resolve_place_in_city,
    search_place_candidates,
    search_pois,
    search_pois_around_location,
)
from agents.schemas import (
    BookingRequirement,
    Evidence,
    OpeningWindow,
    PlaceCandidate,
    PoiGroup,
    PoiRecommendation,
    TravelQuery,
)
from services.travel_search import discover_travel_places
from utils.weather_utils import has_amap_key


CN_TZ = ZoneInfo("Asia/Shanghai")
FOOD_TYPES = "050000|060000"
LEISURE_TYPES = "110000|120000|160000"


@dataclass(slots=True)
class PoiGroupDiscoveryResult:
    groups: list[PoiGroup]
    evidence: list[Evidence]
    errors: list[str]
    web_search_status: str = "not_requested"
    poi_status: str = "empty"
    place_candidates: list[PlaceCandidate] | None = None
    unresolved_places: list[str] | None = None

    def __iter__(self):
        # Keep the original three-value unpacking seam for callers that do not
        # need adapter status yet.
        yield self.groups
        yield self.evidence
        yield self.errors


@dataclass(slots=True)
class PoiRecommendationResult:
    recommendations: list[PoiRecommendation]
    groups: list[PoiGroup]
    evidence: list[Evidence]
    errors: list[str]
    web_search_status: str = "not_requested"
    poi_status: str = "empty"
    place_candidates: list[PlaceCandidate] | None = None
    unresolved_places: list[str] | None = None

    def __iter__(self):
        # Existing tests and integrations can continue to unpack four values.
        yield self.recommendations
        yield self.groups
        yield self.evidence
        yield self.errors


def _place_candidate(item: dict, query_text: str, index: int) -> PlaceCandidate:
    provider_id = str(item.get("provider_id") or "").strip()
    name = str(item.get("name") or query_text).strip()
    candidate_id = provider_id or _source_id({**item, "name": name}, provider="place")
    return PlaceCandidate(
        candidate_id=candidate_id or f"place_{index}",
        provider_id=provider_id,
        name=name,
        category=str(item.get("category") or ""),
        province=str(item.get("province") or ""),
        city=str(item.get("city") or ""),
        district=str(item.get("district") or ""),
        address=str(item.get("address") or item.get("formatted_address") or ""),
        location=str(item.get("location") or ""),
        distance_from_city_center=str(item.get("distance") or ""),
        opening_status=str(item.get("opening_status") or "unknown"),
        confidence="high" if provider_id and name else "unknown",
        source_ids=list(item.get("source_ids") or []),
        query_text=query_text,
    )


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

    raw_windows = item.get("opening_windows") if isinstance(item.get("opening_windows"), list) else []
    opening_windows = [
        OpeningWindow(
            date=str(window.get("date") or ""),
            target_id=str(window.get("target_id") or item.get("provider_id") or item.get("name") or ""),
            open_time=str(window.get("open_time") or ""),
            close_time=str(window.get("close_time") or ""),
            last_entry_time=window.get("last_entry_time"),
            closed_reason=window.get("closed_reason"),
            source_ids=list(window.get("source_ids") or []),
        )
        for window in raw_windows
        if isinstance(window, dict)
    ]
    raw_booking = item.get("booking_requirement")
    booking = (
        BookingRequirement(
            target_id=str(raw_booking.get("target_id") or item.get("provider_id") or item.get("name") or ""),
            required=bool(raw_booking.get("required")),
            booking_url=raw_booking.get("booking_url"),
            booking_note=str(raw_booking.get("booking_note") or ""),
            booking_status=str(raw_booking.get("booking_status") or "unknown"),
            verification=str(raw_booking.get("verification") or "unknown"),
            source_ids=list(raw_booking.get("source_ids") or []),
        )
        if isinstance(raw_booking, dict)
        else BookingRequirement()
    )
    if (
        not isinstance(raw_booking, dict)
        and any(token in f"{category} {item.get('name', '')}" for token in ("博物馆", "展馆", "演出", "景区"))
    ):
        booking = BookingRequirement(
            target_id=str(item.get("provider_id") or item.get("name") or ""),
            booking_note="当前未获得可靠预约状态，请出发前通过官方渠道确认。",
            booking_status="unknown",
            verification="unknown",
        )

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
            provider_id=str(item.get("provider_id") or ""),
            province=str(item.get("province") or ""),
            city=str(item.get("city") or area),
            district=str(item.get("district") or ""),
            location=str(item.get("location") or ""),
            opening_windows=opening_windows,
            booking_requirement=booking,
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
) -> PoiGroupDiscoveryResult:
    city = (
        query.destination_cities[0]
        if query.destination_scope == "province" and query.destination_cities
        else query.destination or query.city
    )
    if not city:
        return PoiGroupDiscoveryResult(
            groups=[],
            evidence=[],
            errors=["缺少目的地，无法发现附近地点。"],
            web_search_status="failed" if search_enabled else "disabled",
            poi_status="failed",
        )

    groups: list[PoiGroup] = []
    evidence: list[Evidence] = []
    errors: list[str] = []
    web_search_statuses: list[str] = []
    has_map_items = False
    map_failed = False
    place_candidates: list[PlaceCandidate] = []
    unresolved_places: list[str] = []
    anchors = query.named_places[:4] or [""]
    for place in anchors:
        confirmed = next(
            (
                item
                for item in query.resolved_place_candidates
                if item.name == place
                or item.query_text == place
                or place in item.name
                or item.name in place
            ),
            None,
        )
        if confirmed is not None:
            place_candidates.append(confirmed)
            resolved = {
                "provider_id": confirmed.provider_id,
                "name": confirmed.name,
                "category": confirmed.category,
                "province": confirmed.province,
                "city": confirmed.city or city,
                "district": confirmed.district,
                "address": confirmed.address,
                "location": confirmed.location,
                "formatted_address": confirmed.address or confirmed.name,
            }
        else:
            try:
                resolved = resolve_place_in_city(city, place) if place else None
            except Exception as exc:
                resolved = None
                map_failed = True
                errors.append(f"地图地点解析失败：{type(exc).__name__}")
        anchor_name = resolved.get("name", place) if resolved else (place or city)

        # The legacy resolver deliberately returns one result.  Only a
        # provider-backed result with an id enters the multi-candidate path;
        # this keeps adapter/test doubles without ids backward compatible.
        if place and resolved and resolved.get("provider_id") and confirmed is None:
            try:
                candidate_rows = search_place_candidates(city, place, page_size=3)
            except Exception as exc:
                candidate_rows = [resolved]
                errors.append(f"地点候选查询失败：{type(exc).__name__}")
            if not candidate_rows:
                candidate_rows = [resolved]
            candidates_for_place = [
                _place_candidate(item, place, len(place_candidates) + index + 1)
                for index, item in enumerate(candidate_rows)
                if isinstance(item, dict)
            ]
            exact_candidates = [
                item
                for item in candidates_for_place
                if item.name == place or place in item.name or item.name in place
            ]
            place_candidates.extend(candidates_for_place)
            if len(exact_candidates) > 1:
                unresolved_places.append(place)

        food_items: list[dict] = []
        leisure_items: list[dict] = []
        if resolved and resolved.get("location"):
            try:
                food_items = search_pois_around_location(
                    resolved["location"], page_size=4, radius=1800, types=FOOD_TYPES
                )
            except Exception as exc:
                map_failed = True
                errors.append(f"附近餐饮查询失败：{type(exc).__name__}")
            try:
                leisure_items = search_pois_around_location(
                    resolved["location"], page_size=3, radius=2200, types=LEISURE_TYPES
                )
            except Exception as exc:
                map_failed = True
                errors.append(f"附近去处查询失败：{type(exc).__name__}")
        if not food_items:
            try:
                food_items = search_pois(anchor_name, "附近餐厅 美食 咖啡馆", page_size=4)
            except Exception as exc:
                map_failed = True
                errors.append(f"餐饮 POI 查询失败：{type(exc).__name__}")
        if not leisure_items:
            try:
                leisure_items = search_pois(anchor_name, "附近景点 娱乐", page_size=3)
            except Exception as exc:
                map_failed = True
                errors.append(f"景点 POI 查询失败：{type(exc).__name__}")
        items: list[PoiRecommendation] = []
        anchor_poi: PoiRecommendation | None = None
        # Explicit user landmarks must themselves become optimizer inputs.
        # Nearby restaurants/attractions are supplementary recommendations and
        # must never be the only POIs retained for a requested landmark.
        if (
            place
            and resolved
            and resolved.get("provider_id")
            and resolved.get("location")
            and place not in unresolved_places
        ):
            anchor_poi, anchor_source = _to_poi(
                resolved,
                str(resolved.get("category") or "地点"),
                city,
                prefix="用户指定地点",
            )
            items.append(anchor_poi)
            evidence.append(anchor_source)

        has_map_items = has_map_items or bool(food_items or leisure_items or anchor_poi)
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
        try:
            search_result = discover_travel_places(
                city,
                anchor=anchor_name if place else "",
                preferences=query.preferences,
                search_enabled=search_enabled,
                activity_logger=activity_logger,
            )
        except Exception as exc:
            # Web discovery is supplementary.  A search client construction or
            # provider failure must not discard map results already collected
            # for this or earlier anchors.
            errors.append(f"旅行网页搜索失败：{type(exc).__name__}")
            web_search_statuses.append("failed" if search_enabled else "disabled")
            search_result = None
        if search_result is not None:
            evidence.extend(Evidence(**item) for item in search_result.sources)
            errors.extend(search_result.errors)
            web_search_statuses.append(search_result.status)
            for candidate in search_result.candidates[:4]:
                try:
                    resolved_candidate = resolve_place_in_city(city, candidate.get("name_hint", ""))
                except Exception as exc:
                    map_failed = True
                    errors.append(f"网页候选地图验证失败：{type(exc).__name__}")
                    continue
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

    web_search_status = _combine_search_statuses(web_search_statuses, search_enabled)
    poi_status = (
        "partial" if has_map_items and map_failed
        else "success" if has_map_items or groups
        else "not_configured" if not has_amap_key()
        else "failed" if map_failed
        else "empty"
    )
    if groups and poi_status == "empty":
        poi_status = "success"
    return PoiGroupDiscoveryResult(
        groups=groups,
        evidence=_dedupe_evidence(evidence),
        errors=_dedupe_errors(errors),
        web_search_status=web_search_status,
        poi_status=poi_status,
        place_candidates=place_candidates,
        unresolved_places=list(dict.fromkeys(unresolved_places)),
    )


def _combine_search_statuses(statuses: list[str], search_enabled: bool) -> str:
    if not search_enabled:
        return "disabled"
    if not statuses:
        return "empty"
    normalized = set(statuses)
    if "partial" in normalized:
        return "partial"
    if "success" in normalized and normalized.intersection({"failed", "not_configured"}):
        return "partial"
    if "success" in normalized:
        return "success"
    if "not_configured" in normalized:
        return "not_configured"
    if "failed" in normalized:
        return "failed"
    return "empty"


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
) -> PoiRecommendationResult:
    city = (
        query.destination_cities[0]
        if query.destination_scope == "province" and query.destination_cities
        else query.destination or query.city
    )
    if not city:
        return PoiRecommendationResult(
            recommendations=[],
            groups=[],
            evidence=[],
            errors=["缺少目的地，无法推荐 POI。"],
            web_search_status="failed" if search_enabled else "disabled",
            poi_status="failed",
        )

    group_result = build_poi_groups(
        query,
        search_enabled=search_enabled,
        activity_logger=activity_logger,
    )
    if isinstance(group_result, PoiGroupDiscoveryResult):
        poi_groups = group_result.groups
        evidence = group_result.evidence
        errors = group_result.errors
        web_search_status = group_result.web_search_status
        poi_status = group_result.poi_status
        place_candidates = list(group_result.place_candidates or [])
        unresolved_places = list(group_result.unresolved_places or [])
    else:
        poi_groups, evidence, errors = group_result
        web_search_status = "disabled" if not search_enabled else ("failed" if errors else "empty")
        poi_status = "success" if poi_groups else ("failed" if errors else "empty")
        place_candidates = []
        unresolved_places = []

    recommendations: list[PoiRecommendation] = []
    for group in poi_groups:
        recommendations.extend(group.items)

    # These are map-backed city-level candidates and work even when web search
    # is disabled.  Ratings are shown only if the adapter actually returned one.
    city_lookup_failed = False
    try:
        food_items = search_pois(city, "本地特色餐厅 美食", page_size=4)
    except Exception as exc:
        food_items = []
        city_lookup_failed = True
        errors.append(f"城市餐饮 POI 查询失败：{type(exc).__name__}")
    try:
        sight_items = search_pois(city, "热门景点 旅游", page_size=4)
    except Exception as exc:
        sight_items = []
        city_lookup_failed = True
        errors.append(f"城市景点 POI 查询失败：{type(exc).__name__}")
    for item in _sort_by_travel_fit(food_items, query.preferences):
        poi, source = _to_poi(item, "美食", city)
        recommendations.append(poi)
        evidence.append(source)
    for item in _sort_by_travel_fit(sight_items, query.preferences):
        poi, source = _to_poi(item, "景点", city)
        recommendations.append(poi)
        evidence.append(source)

    deduped = _dedupe_items(recommendations)
    if deduped:
        poi_status = "partial" if city_lookup_failed or poi_status == "partial" else "success"
    elif not has_amap_key():
        poi_status = "not_configured"
    elif city_lookup_failed or poi_status in {"failed", "partial"}:
        poi_status = "failed" if not recommendations else "partial"
    elif poi_status not in {"failed", "not_configured"}:
        poi_status = "empty"
    if not deduped:
        errors.append("当前未获取到可验证的地图 POI；未生成虚构地点占位卡。")
    return PoiRecommendationResult(
        recommendations=deduped[:16],
        groups=poi_groups,
        evidence=_dedupe_evidence(evidence),
        errors=_dedupe_errors(errors),
        web_search_status=web_search_status,
        poi_status=poi_status,
        place_candidates=place_candidates,
        unresolved_places=list(dict.fromkeys(unresolved_places)),
    )
