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


def _cost_value(value: object) -> float | None:
    text = str(value or "")
    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else None


def _place_id(place: PoiRecommendation) -> str:
    return str(place.provider_id or place.name).strip()


def _route_key(origin: str, destination: str) -> tuple[str, str]:
    return (str(origin or "").strip(), str(destination or "").strip())


def _route_matrix(routes: list[RoutePlan]) -> dict[tuple[str, str], RoutePlan]:
    return {
        _route_key(route.origin, route.destination): route
        for route in routes
        if route.origin and route.destination
    }


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
        usable = ordered or usable
    seen: set[str] = set()
    result: list[PoiRecommendation] = []
    for item in usable:
        identifier = _place_id(item)
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append(item)
        if len(result) >= MAX_ENUMERATED_PLACES:
            break
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
) -> tuple[float, int, int, float, list[RoutePlan]]:
    time_weight, walking_weight, cost_weight, pace_weight = _weights(query)
    total_time = 0
    total_walking = 0
    total_cost = 0.0
    missing_edges = 0
    selected_routes: list[RoutePlan] = []
    for origin, destination in zip(order, order[1:]):
        route = matrix.get(_route_key(origin.name, destination.name))
        if route is None:
            missing_edges += 1
            total_time += 10_000
            continue
        duration = _duration_minutes(route.duration) or 10_000
        total_time += duration
        distance = _distance_meters(route.distance) or 0
        if route.mode == "walking":
            total_walking += duration
        elif "步行" in route.summary:
            total_walking += max(1, round(distance / 80))
        price = _cost_value(route.summary)
        if price is not None:
            total_cost += price
        selected_routes.append(route)
    pace_penalty = max(0, len(order) - 3) * (1 if query.pace == "relaxed" else 0)
    score = (
        time_weight * total_time
        + walking_weight * total_walking
        + cost_weight * total_cost
        + pace_weight * pace_penalty
        + missing_edges * 10_000
    )
    return score, total_time, total_walking, total_cost, selected_routes


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
            route = matrix.get(_route_key(previous.name, place.name))
            if route is None:
                conflicts.append(f"地点“{previous.name}”与“{place.name}”之间缺少已验证路线。")
                return False, {}, conflicts
            transit_minutes = _duration_minutes(route.duration) or 10_000
            day_transit[day] += transit_minutes
            cursor += transit_minutes + DEFAULT_BUFFER_MINUTES
        window = _window_for(place, day)
        if window is not None:
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


def optimize_itinerary(
    query: TravelQuery,
    places: list[PoiRecommendation],
    route_plans: list[RoutePlan] | None = None,
    *,
    locked_titles: list[str] | None = None,
) -> ItineraryOptimizationResult:
    candidates = _candidate_places(query, places)
    if not candidates:
        return ItineraryOptimizationResult(
            diagnostics=[OptimizationDiagnostic("no_places", "没有足够的已验证地点可供排序。", "warning")]
        )

    routes = list(route_plans or [])
    matrix = _route_matrix(routes)
    diagnostics: list[OptimizationDiagnostic] = []
    if len(candidates) > MAX_ENUMERATED_PLACES:
        diagnostics.append(
            OptimizationDiagnostic("beam_search_fallback", "地点超过 5 个，已截取前 5 个候选进行 MVP 排序。", "warning")
        )
    locked = [str(item).strip() for item in (locked_titles or []) if str(item).strip()]
    if locked and any(item.name in locked for item in candidates):
        permutations = [tuple(candidates)]
        diagnostics.append(
            OptimizationDiagnostic("locked_order_preserved", "检测到已锁定地点，保留当前顺序。", "info")
        )
    elif len(candidates) <= MAX_ENUMERATED_PLACES:
        permutations = list(itertools.permutations(candidates))
    else:
        permutations = [tuple(candidates)]

    best: tuple[float, tuple[PoiRecommendation, ...], dict[str, tuple[str, str, str]], list[str]] | None = None
    for order in permutations:
        feasible, schedule, conflicts = _simulate_schedule(order, matrix, query)
        if not feasible:
            continue
        score, _time, _walking, _cost, _selected = _score_order(order, matrix, query)
        if best is None or score < best[0]:
            best = (score, order, schedule, conflicts)

    if best is None:
        diagnostics.append(
            OptimizationDiagnostic("no_feasible_order", "没有同时满足路线或营业时间约束的顺序。", "warning")
        )
        return ItineraryOptimizationResult(
            ordered_places=candidates,
            diagnostics=diagnostics,
            conflicts=[f"未能安排地点：{item.name}" for item in candidates],
        )

    score, order, schedule, conflicts = best
    segments: list[RouteSegment] = []
    for index, (origin, destination) in enumerate(zip(order, order[1:]), start=1):
        route = matrix.get(_route_key(origin.name, destination.name))
        if route is None:
            continue
        day = schedule.get(_place_id(destination), (query.start_date, "", ""))[0]
        duration = _duration_minutes(route.duration)
        segments.append(
            RouteSegment(
                segment_id="segment_" + sha1(f"{origin.name}|{destination.name}|{day}".encode()).hexdigest()[:12],
                date=day,
                origin_place_id=_place_id(origin),
                destination_place_id=_place_id(destination),
                mode=route.mode,
                distance_meters=_distance_meters(route.distance),
                duration_minutes=duration,
                estimated_cost=str(_cost_value(route.summary)) if _cost_value(route.summary) is not None else None,
                buffer_minutes=DEFAULT_BUFFER_MINUTES,
                walking_minutes=duration if route.mode == "walking" else None,
                source_ids=[],
            )
        )
    if not matrix and len(order) > 1:
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
        schedule=schedule,
        score=score,
        diagnostics=diagnostics,
        conflicts=conflicts,
    )
