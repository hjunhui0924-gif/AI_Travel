from __future__ import annotations

import re

from adapters.amap_adapter import plan_route, resolve_place_in_city
from agents.schemas import RoutePlan, TravelQuery


class RoutePlansResult(list[RoutePlan]):
    """List-compatible route result with provider partial-failure metadata."""

    def __init__(self, items: list[RoutePlan] | None = None, *, errors: list[str] | None = None):
        super().__init__(items or [])
        self.errors = list(errors or [])

    @property
    def partial(self) -> bool:
        return bool(self.errors)


def _dedupe_places(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        cleaned = (item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _parse_duration_minutes(value: str) -> int:
    if not value:
        return 10**9
    match = re.search(r"(\d+)", value)
    if match:
        return int(match.group(1))
    return 10**9


def _parse_distance_meters(value: str) -> int:
    if not value:
        return 10**9
    match = re.search(r"(\d+)", value)
    if match:
        return int(match.group(1))
    return 10**9


def _resolve_places(city: str, places: list[str]) -> list[dict]:
    resolved_places = []
    for place in places:
        resolved = resolve_place_in_city(city, place)
        if resolved:
            resolved_places.append(resolved)
        else:
            resolved_places.append(
                {
                    "name": place,
                    "address": "",
                    "formatted_address": place,
                    "city": city,
                    "location": "",
                }
            )
    return resolved_places


def _build_segments(query: TravelQuery) -> list[tuple[dict, dict]]:
    city = (
        query.destination_cities[0]
        if query.destination_scope == "province" and query.destination_cities
        else query.destination or query.city
    )
    named_places = _dedupe_places(query.named_places)
    if not city or not named_places:
        return []

    resolved_places = _resolve_places(city, named_places)
    segments: list[tuple[dict, dict]] = []
    for start, end in zip(resolved_places, resolved_places[1:]):
        start_key = (start.get("name", ""), start.get("formatted_address", ""), start.get("location", ""))
        end_key = (end.get("name", ""), end.get("formatted_address", ""), end.get("location", ""))
        # Do not invent a route for an unresolved landmark. AMap's route
        # geometry is only trustworthy after both endpoints are location
        # resolved in the requested city.
        if start_key == end_key or not start.get("location") or not end.get("location"):
            continue
        segments.append((start, end))
    return segments


def get_route_plans(query: TravelQuery) -> RoutePlansResult:
    segments = _build_segments(query)
    if not segments:
        return RoutePlansResult()

    results = RoutePlansResult()
    errors: list[str] = []
    for start, end in segments:
        candidates: list[RoutePlan] = []
        for mode in ["transit", "driving", "walking"]:
            try:
                # Use the city-scoped coordinates resolved above. Passing a
                # bare landmark name to geocoding can select a same-named
                # place in another city; the adapter returns the geometry and
                # the route model keeps the display names separately.
                payload = plan_route(start["location"], end["location"], strategy=mode)
            except Exception as exc:
                # Route modes are independent provider calls.  A transit
                # outage must not prevent driving/walking alternatives from
                # being considered for the same pair of places.
                errors.append(f"{mode}:{type(exc).__name__}")
                continue
            if not isinstance(payload, dict) or not payload:
                continue
            candidates.append(
                RoutePlan(
                    mode=mode,
                    origin=start.get("name") or payload.get("origin", ""),
                    destination=end.get("name") or payload.get("destination", ""),
                    origin_address=start.get("formatted_address", start["name"]),
                    destination_address=end.get("formatted_address", end["name"]),
                    duration=payload.get("duration", ""),
                    distance=payload.get("distance", ""),
                    summary=payload.get("summary", ""),
                    origin_location=payload.get("origin_location", start.get("location", "")),
                    destination_location=payload.get("destination_location", end.get("location", "")),
                    polyline=payload.get("polyline", []) if isinstance(payload.get("polyline", []), list) else [],
                )
            )

        if candidates:
            usable = []
            for item in candidates:
                distance = _parse_distance_meters(item.distance)
                if item.mode == "walking" and distance > 3000:
                    continue
                usable.append(item)
            if not usable:
                usable = [item for item in candidates if item.mode != "walking"] or candidates
            best = min(usable, key=lambda item: _parse_duration_minutes(item.duration))
            results.append(best)

    results.errors = errors
    return results
