"""Deterministic, provider-fact-only itinerary ordering for the MVP."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha1

from agents.schemas import (
    OptimizationDiagnostic,
    PoiRecommendation,
    RoutePlan,
    RouteSegment,
    TravelQuery,
)


MAX_ENUMERATED_PLACES = 5
DEFAULT_BUFFER_MINUTES = 20
DEFAULT_VISIT_MINUTES = 90


@dataclass(slots=True)
class ItineraryOptimizationResult:
    ordered_places: list[PoiRecommendation] = field(default_factory=list)
    route_segments: list[RouteSegment] = field(default_factory=list)
    selected_route_plans: list[RoutePlan] = field(default_factory=list)
    schedule: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    score: float | None = None
    diagnostics: list[OptimizationDiagnostic] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def _duration_minutes(value: object) -> int | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    hour = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|小時|h)", text)
    minute = re.search(r"(\d+)\s*(?:分钟|分鐘|min|m)", text)
    result = 0
    if hour:
        result += round(float(hour.group(1)) * 60)
    if minute:
        result += int(minute.group(1))
    if result:
        return result
    digits = re.search(r"\d+", text)
    return int(digits.group(0)) if digits else None


def _distance_meters(value: object) -> int | None:
    text = str(value or "").replace(",", "")
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _route_duration(route: RoutePlan) -> int | None:
    return route.duration_minutes if route.duration_minutes is not None else _duration_minutes(route.duration)


def _route_distance(route: RoutePlan) -> int | None:
    return route.distance_meters if route.distance_meters is not None else _distance_meters(route.distance)


def _place_id(place: PoiRecommendation) -> str:
    return str(place.provider_id or place.name).strip()


def _route_key(origin: str, destination: str) -> tuple[str, str]:
    return (str(origin or "").strip(), str(destination or "").strip())


def _route_endpoint(route: RoutePlan, *, origin: bool) -> str:
    value = route.origin_place_id if origin else route.destination_place_id
    return str(value or (route.origin if origin else route.destination) or "").strip()


def _route_for(
    matrix: dict[tuple[str, str], RoutePlan],
    origin: PoiRecommendation,
    destination: PoiRecommendation,
) -> RoutePlan | None:
    origin_id = _place_id(origin)
    destination_id = _place_id(destination)
    return matrix.get(_route_key(origin_id, destination_id)) or matrix.get(
        _route_key(origin.name, destination.name)
    )


def _select_route_candidate(candidates: list[RoutePlan], query: TravelQuery) -> RoutePlan:
    def duration(item: RoutePlan) -> int:
        return _route_duration(item) or 10**9

    def distance(item: RoutePlan) -> int:
        return _route_distance(item) or 10**9

    if query.objective == "cheapest":
        known_costs = [item for item in candidates if item.estimated_cost is not None]
        pool = known_costs or candidates
        return min(pool, key=lambda item: (item.estimated_cost if item.estimated_cost is not None else float("inf"), duration(item)))
    if query.objective == "least_walking":
        return min(
            candidates,
            key=lambda item: (
                0 if item.mode == "walking" else 1,
                distance(item),
                duration(item),
            ),
        )
    return min(candidates, key=duration)


def _route_matrix(
    routes: list[RoutePlan],
    query: TravelQuery,
    alternatives: dict[tuple[str, str], list[RoutePlan]] | None = None,
) -> dict[tuple[str, str], RoutePlan]:
    matrix: dict[tuple[str, str], RoutePlan] = {}
    for route in routes:
        if not route.origin or not route.destination:
            continue
        matrix[_route_key(_route_endpoint(route, origin=True), _route_endpoint(route, origin=False))] = route
        # Keep a display-name fallback for legacy route snapshots that do not
        # carry stable endpoint IDs.
        matrix.setdefault(_route_key(route.origin, route.destination), route)
    for edge_key, candidates in (alternatives or {}).items():
        if not candidates:
            continue
        selected = _select_route_candidate(candidates, query)
        matrix[edge_key] = selected
        matrix[_route_key(_route_endpoint(selected, origin=True), _route_endpoint(selected, origin=False))] = selected
        matrix.setdefault(_route_key(selected.origin, selected.destination), selected)
    return matrix


def _time_to_minutes(value: str, default: int) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not match:
        return default
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return default
    return hour * 60 + minute


def _clock(value: int) -> str:
    value = max(0, min(24 * 60 - 1, value))
    return f"{value // 60:02d}:{value % 60:02d}"


def _window_for(place: PoiRecommendation, day: str):
    for window in place.opening_windows:
        if not window.date or window.date == day:
            return window
    return None


def _candidate_places(query: TravelQuery, places: list[PoiRecommendation]) -> list[PoiRecommendation]:
    usable = [item for item in places if item.name and not item.is_placeholder]
    if query.named_places:
        ordered: list[PoiRecommendation] = []
        for requested in query.named_places:
            matches = [item for item in usable if requested in item.name or item.name in requested]
            if matches:
                ordered.append(matches[0])
        # Explicit user landmarks are a hard coverage requirement. If one is
        # missing from verified POI data, keep the verified subset and let the
        # caller expose a conflict instead of replacing it with a nearby
        # restaurant or arbitrary recommendation.
        usable = ordered
    seen: set[str] = set()
    result: list[PoiRecommendation] = []
    for item in usable:
        identifier = _place_id(item)
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append(item)
    return result


def _weights(query: TravelQuery) -> tuple[float, float, float, float]:
    # time, walking, cost, pace penalty
    if query.objective == "fastest":
        return 1.0, 0.15, 0.02, 0.1
    if query.objective == "cheapest":
        return 0.2, 0.15, 1.0, 0.1
    if query.objective == "least_walking":
        return 0.35, 1.0, 0.1, 0.1
    return 0.55, 0.45, 0.25, 0.15


def _score_order(
    order: tuple[PoiRecommendation, ...],
    matrix: dict[tuple[str, str], RoutePlan],
    query: TravelQuery,
) -> tuple[float, int, int, float, list[RoutePlan], int]:
    time_weight, walking_weight, cost_weight, pace_weight = _weights(query)
    total_time = 0
    total_walking = 0
    total_cost = 0.0
    missing_edges = 0
    selected_routes: list[RoutePlan] = []
    unknown_costs = 0
    for origin, destination in zip(order, order[1:]):
        route = _route_for(matrix, origin, destination)
        if route is None:
            missing_edges += 1
            total_time += 10_000
            continue
        duration = _route_duration(route) or 10_000
        total_time += duration
        distance = _route_distance(route) or 0
        if route.mode == "walking":
            total_walking += duration
        elif "步行" in route.summary:
            total_walking += max(1, round(distance / 80))
        if route.estimated_cost is not None:
            total_cost += route.estimated_cost
        else:
            unknown_costs += 1
        selected_routes.append(route)
    pace_penalty = max(0, len(order) - 3) * (1 if query.pace == "relaxed" else 0)
    score = (
        time_weight * total_time
        + walking_weight * total_walking
        + cost_weight * total_cost
        + pace_weight * pace_penalty
        + missing_edges * 10_000
        + unknown_costs * (1_000_000 if query.objective == "cheapest" else 0)
    )
    return score, total_time, total_walking, total_cost, selected_routes, unknown_costs


def _dates(query: TravelQuery) -> list[str]:
    try:
        start = date.fromisoformat(query.start_date)
        end = date.fromisoformat(query.end_date or query.start_date)
    except ValueError:
        return [query.start_date or ""]
    return [
        (start + timedelta(days=index)).isoformat()
        for index in range(min(31, max(1, (end - start).days + 1)))
    ]


def _simulate_schedule(
    order: tuple[PoiRecommendation, ...],
    matrix: dict[tuple[str, str], RoutePlan],
    query: TravelQuery,
) -> tuple[bool, dict[str, tuple[str, str, str]], list[str]]:
    dates = _dates(query)
    schedule: dict[str, tuple[str, str, str]] = {}
    conflicts: list[str] = []
    day_cursors = {day: 9 * 60 for day in dates}
    day_transit = {day: 0 for day in dates}
    for index, place in enumerate(order):
        day = dates[min(index // 4, len(dates) - 1)]
        cursor = day_cursors[day]
        previous = order[index - 1] if index and index // 4 == (index - 1) // 4 else None
        if previous is not None:
            route = _route_for(matrix, previous, place)
            if route is None:
                conflicts.append(f"地点“{previous.name}”与“{place.name}”之间缺少已验证路线。")
                return False, {}, conflicts
            transit_minutes = _route_duration(route) or 10_000
            day_transit[day] += transit_minutes
            cursor += transit_minutes + DEFAULT_BUFFER_MINUTES
        window = _window_for(place, day)
        if window is not None:
            if window.closed_reason:
                conflicts.append(f"地点“{place.name}”在 {day} 不可安排：{window.closed_reason}。")
                return False, {}, conflicts
            open_at = _time_to_minutes(window.open_time, 0)
            close_at = _time_to_minutes(window.close_time, 24 * 60 - 1)
            cursor = max(cursor, open_at)
            last_entry = _time_to_minutes(window.last_entry_time or "", close_at)
            if cursor > last_entry or cursor + DEFAULT_VISIT_MINUTES > close_at:
                conflicts.append(f"地点“{place.name}”在 {day} 无法满足营业时间或最后入场时间。")
                return False, {}, conflicts
        end = cursor + DEFAULT_VISIT_MINUTES
        if query.max_daily_transit_minutes is not None and day_transit[day] > query.max_daily_transit_minutes:
            conflicts.append(f"{day} 的通勤与活动节奏超过用户限制。")
            return False, {}, conflicts
        schedule[_place_id(place)] = (day, _clock(cursor), _clock(end))
        day_cursors[day] = end
    return True, schedule, conflicts


def _build_route_segments(
    order: tuple[PoiRecommendation, ...],
    matrix: dict[tuple[str, str], RoutePlan],
    schedule: dict[str, tuple[str, str, str]],
    query: TravelQuery,
) -> list[RouteSegment]:
    segments: list[RouteSegment] = []
    for origin, destination in zip(order, order[1:]):
        route = _route_for(matrix, origin, destination)
        if route is None:
            continue
        day = schedule.get(_place_id(destination), (query.start_date, "", ""))[0]
        duration = _route_duration(route)
        segments.append(
            RouteSegment(
                segment_id="segment_"
                + sha1(
                    f"{_place_id(origin)}|{_place_id(destination)}|{day}".encode()
                ).hexdigest()[:12],
                date=day,
                origin_place_id=_place_id(origin),
                destination_place_id=_place_id(destination),
                mode=route.mode,
                distance_meters=_route_distance(route),
                duration_minutes=duration,
                estimated_cost=(
                    str(route.estimated_cost)
                    if route.estimated_cost is not None
                    else None
                ),
                buffer_minutes=DEFAULT_BUFFER_MINUTES,
                walking_minutes=duration if route.mode == "walking" else None,
                source_ids=list(route.source_ids),
            )
        )
    return segments


def optimize_itinerary(
    query: TravelQuery,
    places: list[PoiRecommendation],
    route_plans: list[RoutePlan] | None = None,
    *,
    locked_titles: list[str] | None = None,
    route_alternatives: dict[tuple[str, str], list[RoutePlan]] | None = None,
) -> ItineraryOptimizationResult:
    candidates = _candidate_places(query, places)
    if not candidates:
        return ItineraryOptimizationResult(
            diagnostics=[OptimizationDiagnostic("no_places", "没有足够的已验证地点可供排序。", "warning")]
        )

    routes = list(route_plans or [])
    if route_alternatives is None:
        route_alternatives = getattr(route_plans, "alternatives", None)
    matrix = _route_matrix(routes, query, route_alternatives)
    diagnostics: list[OptimizationDiagnostic] = []
    if query.named_places:
        matched = {item.name for item in candidates}
        missing = [item for item in query.named_places if item not in matched]
        if missing:
            diagnostics.append(
                OptimizationDiagnostic(
                    "required_places_missing",
                    "以下用户指定地点没有通过地图/POI验证，未用其他地点替换：" + "、".join(missing),
                    "warning",
                )
            )
    if len(candidates) > MAX_ENUMERATED_PLACES:
        diagnostics.append(
            OptimizationDiagnostic(
                "beam_search_fallback",
                "地点超过 5 个，未枚举全部顺序；保留所有用户地点并沿用已验证的顺序边。",
                "warning",
            )
        )
    locked = [str(item).strip() for item in (locked_titles or []) if str(item).strip()]
    explicit_order_has_edges = all(
        _route_for(matrix, origin, destination) is not None
        for origin, destination in zip(candidates, candidates[1:])
    )
    complete_route_matrix = all(
        _route_for(matrix, origin, destination) is not None
        for origin in candidates
        for destination in candidates
        if origin is not destination
    )
    if locked and any(item.name in locked for item in candidates):
        permutations = [tuple(candidates)]
        diagnostics.append(
            OptimizationDiagnostic("locked_order_preserved", "检测到已锁定地点，保留当前顺序。", "info")
        )
    elif query.named_places and explicit_order_has_edges and not complete_route_matrix:
        # A route provider may return only the user's requested adjacent
        # segments. Preserve that verified order instead of enumerating
        # permutations that require edges we never queried.
        permutations = [tuple(candidates)]
        if len(candidates) > 2:
            diagnostics.append(
                OptimizationDiagnostic(
                    "route_matrix_partial",
                    "路线数据只覆盖用户指定顺序，已保留该顺序，未声称全局最优。",
                    "warning",
                )
            )
    elif len(candidates) <= MAX_ENUMERATED_PLACES:
        permutations = list(itertools.permutations(candidates))
    else:
        permutations = [tuple(candidates)]

    best: tuple[
        float,
        tuple[PoiRecommendation, ...],
        dict[str, tuple[str, str, str]],
        list[str],
        list[RoutePlan],
        int,
    ] | None = None
    for order in permutations:
        feasible, schedule, conflicts = _simulate_schedule(order, matrix, query)
        if not feasible:
            continue
        score, _time, _walking, _cost, selected, unknown_costs = _score_order(order, matrix, query)
        if best is None or score < best[0]:
            best = (score, order, schedule, conflicts, selected, unknown_costs)

    if best is None:
        diagnostics.append(
            OptimizationDiagnostic("no_feasible_order", "没有同时满足路线或营业时间约束的顺序。", "warning")
        )
        fallback_order = tuple(candidates)
        fallback_routes = [
            _route_for(matrix, origin, destination)
            for origin, destination in zip(fallback_order, fallback_order[1:])
        ]
        fallback_routes = [route for route in fallback_routes if route is not None]
        if len(fallback_routes) < max(0, len(fallback_order) - 1):
            diagnostics.append(
                OptimizationDiagnostic(
                    "route_matrix_partial",
                    "路线矩阵存在缺边，仅保留已查询到的真实路线段。",
                    "warning",
                )
            )
        return ItineraryOptimizationResult(
            ordered_places=candidates,
            route_segments=_build_route_segments(fallback_order, matrix, {}, query),
            selected_route_plans=fallback_routes,
            diagnostics=diagnostics,
            conflicts=[f"未能安排地点：{item.name}" for item in candidates],
        )

    score, order, schedule, conflicts, selected_routes, unknown_costs = best
    segments = _build_route_segments(order, matrix, schedule, query)
    if unknown_costs and query.objective == "cheapest":
        diagnostics.append(
            OptimizationDiagnostic(
                "cost_unknown",
                "部分路线没有可靠费用，未将未知费用当作 0 元；当前结果不能宣称最低价。",
                "warning",
            )
        )
    if not complete_route_matrix and len(order) > 1:
        diagnostics.append(
            OptimizationDiagnostic("route_matrix_missing", "路线服务未返回完整矩阵，已保留用户地点顺序，未声称绝对最优。", "warning")
        )
    diagnostics.append(
        OptimizationDiagnostic(
            "objective_applied",
            f"按 {query.objective} 目标和 {query.pace} 节奏完成确定性排序。",
            "info",
        )
    )
    return ItineraryOptimizationResult(
        ordered_places=list(order),
        route_segments=segments,
        selected_route_plans=selected_routes,
        schedule=schedule,
        score=score,
        diagnostics=diagnostics,
        conflicts=conflicts,
    )
