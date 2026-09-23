from __future__ import annotations

import copy
import os
import re
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from hashlib import sha1
from threading import RLock

from adapters.amap_adapter import plan_route, resolve_place_in_city
from agents.schemas import RoutePlan, TravelQuery


_ROUTE_CACHE_VERSION = "amap-route-v1"
_ROUTE_CACHE_LOCK = RLock()
_ROUTE_CACHE: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_ROUTE_INFLIGHT: dict[str, Future] = {}


def clear_route_cache() -> None:
    """Clear process-local route cache and in-flight request registry.

    This is intentionally small and public so tests and a single-worker
    shutdown hook can discard provider data without touching user storage.
    """

    with _ROUTE_CACHE_LOCK:
        _ROUTE_CACHE.clear()
        _ROUTE_INFLIGHT.clear()


def _cache_ttl_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("ROUTE_MATRIX_CACHE_TTL_SECONDS", "600")))
    except ValueError:
        return 600.0


def _cache_max_entries() -> int:
    try:
        return max(1, int(os.getenv("ROUTE_MATRIX_CACHE_MAX_ENTRIES", "256")))
    except ValueError:
        return 256


def _cache_key(
    city: str,
    origin: str,
    destination: str,
    mode: str,
    departure_bucket: str,
) -> str:
    coordinate_version = os.getenv("ROUTE_COORDINATE_VERSION", "amap-v1")
    return "|".join(
        (
            _ROUTE_CACHE_VERSION,
            coordinate_version,
            city.strip(),
            origin.strip(),
            destination.strip(),
            mode.strip(),
            departure_bucket.strip() or "static",
        )
    )


def _cached_plan_route(
    origin: str,
    destination: str,
    *,
    strategy: str,
    city: str,
    departure_bucket: str,
) -> tuple[dict | None, str]:
    """Fetch one route with success-only TTL caching and request coalescing."""

    key = _cache_key(city, origin, destination, strategy, departure_bucket)
    now = time.monotonic()
    with _ROUTE_CACHE_LOCK:
        cached = _ROUTE_CACHE.get(key)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                _ROUTE_CACHE.move_to_end(key)
                return copy.deepcopy(payload), "cache"
            _ROUTE_CACHE.pop(key, None)

        future = _ROUTE_INFLIGHT.get(key)
        owner = future is None
        if owner:
            future = Future()
            _ROUTE_INFLIGHT[key] = future

    if not owner:
        payload, error = future.result()
        if error is not None:
            raise error
        return copy.deepcopy(payload) if isinstance(payload, dict) else None, "coalesced"

    payload: dict | None = None
    error: Exception | None = None
    try:
        raw_payload = plan_route(origin, destination, strategy=strategy)
        if isinstance(raw_payload, dict) and raw_payload:
            payload = copy.deepcopy(raw_payload)
    except Exception as exc:  # noqa: BLE001 - preserve provider exception type
        error = exc

    with _ROUTE_CACHE_LOCK:
        if error is None and payload is not None and _cache_ttl_seconds() > 0:
            _ROUTE_CACHE[key] = (time.monotonic() + _cache_ttl_seconds(), copy.deepcopy(payload))
            _ROUTE_CACHE.move_to_end(key)
            while len(_ROUTE_CACHE) > _cache_max_entries():
                _ROUTE_CACHE.popitem(last=False)
        current = _ROUTE_INFLIGHT.pop(key, None)
        if current is not None and not current.done():
            # Store the error as data so a failed provider request is never
            # cached, while concurrent waiters still receive the same result.
            current.set_result((payload, error))

    if error is not None:
        raise error
    return copy.deepcopy(payload) if payload is not None else None, "miss"


class RoutePlansResult(list[RoutePlan]):
    """List-compatible route result with provider partial-failure metadata."""

    def __init__(
        self,
        items: list[RoutePlan] | None = None,
        *,
        errors: list[str] | None = None,
        complete: bool = False,
        attempted_requests: int = 0,
        missing_edges: list[tuple[str, str]] | None = None,
        missing_edge_reasons: dict[tuple[str, str], str] | None = None,
        alternatives: dict[tuple[str, str], list[RoutePlan]] | None = None,
        cache_hits: int = 0,
        coalesced_requests: int = 0,
    ):
        super().__init__(items or [])
        self.errors = list(errors or [])
        self.complete = bool(complete)
        self.attempted_requests = max(0, int(attempted_requests))
        self.missing_edges = list(missing_edges or [])
        self.missing_edge_reasons = dict(missing_edge_reasons or {})
        self.alternatives = {
            key: list(value)
            for key, value in (alternatives or {}).items()
        }
        self.cache_hits = max(0, int(cache_hits))
        self.coalesced_requests = max(0, int(coalesced_requests))

    @property
    def partial(self) -> bool:
        return bool(self.errors or self.missing_edges)


def _dedupe_places(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        cleaned = (item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _parse_duration_minutes(value: str) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return 10**9
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|小時|h)", text)
    minutes = re.search(r"(\d+)\s*(?:分钟|分鐘|min|m)", text)
    if hours or minutes:
        total = 0.0
        if hours:
            total += float(hours.group(1)) * 60
        if minutes:
            total += int(minutes.group(1))
        return max(1, round(total))
    match = re.search(r"(\d+)", text)
    if match:
        return int(match.group(1))
    return 10**9


def _parse_distance_meters(value: str) -> int:
    text = str(value or "").strip().lower().replace(",", "")
    if not text:
        return 10**9
    match = re.search(r"(\d+(?:\.\d+)?)\s*(公里|千米|km|米|m)?", text)
    if match:
        distance = float(match.group(1))
        if match.group(2) in {"公里", "千米", "km"}:
            distance *= 1000
        return round(distance)
    return 10**9


def _route_duration_minutes(route: RoutePlan) -> int:
    return route.duration_minutes if route.duration_minutes is not None else _parse_duration_minutes(route.duration)


def _place_identity(place: dict) -> str:
    """Return the stable identity used by matrix edges and optimizer keys."""

    for key in ("provider_id", "candidate_id", "location"):
        value = str(place.get(key) or "").strip()
        if value:
            return value
    name = str(place.get("name") or "").strip()
    address = str(place.get("formatted_address") or place.get("address") or "").strip()
    return "|".join(part for part in (name, address) if part)


def _route_source_id(origin_id: str, destination_id: str, mode: str, bucket: str) -> str:
    raw = f"{origin_id}|{destination_id}|{mode}|{bucket}".encode("utf-8", errors="ignore")
    return "route_" + sha1(raw).hexdigest()[:12]


def _confirmed_place_for(query: TravelQuery, requested: str):
    exact = [
        candidate
        for candidate in query.resolved_place_candidates
        if str(getattr(candidate, "query_text", "") or "").strip() == requested
        or str(getattr(candidate, "name", "") or "").strip() == requested
    ]
    return exact[0] if len(exact) == 1 else None


def _resolve_places(city: str, places: list[str], query: TravelQuery | None = None) -> list[dict]:
    resolved_places = []
    for place in places:
        confirmed = _confirmed_place_for(query, place) if query is not None else None
        if confirmed is not None:
            provider_id = str(getattr(confirmed, "provider_id", "") or "").strip()
            candidate_id = str(getattr(confirmed, "candidate_id", "") or "").strip()
            resolved = {
                "provider_id": provider_id or candidate_id,
                "candidate_id": candidate_id,
                "name": str(getattr(confirmed, "name", "") or place).strip(),
                "address": str(getattr(confirmed, "address", "") or "").strip(),
                "formatted_address": str(
                    getattr(confirmed, "address", "") or getattr(confirmed, "name", "") or place
                ).strip(),
                "city": str(getattr(confirmed, "city", "") or city).strip(),
                "location": str(getattr(confirmed, "location", "") or "").strip(),
            }
        else:
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


def _build_segments(
    query: TravelQuery,
    *,
    complete_matrix: bool = False,
) -> list[tuple[dict, dict]]:
    city = (
        query.destination_cities[0]
        if query.destination_scope == "province" and query.destination_cities
        else query.destination or query.city
    )
    named_places = _dedupe_places(query.named_places)
    if not city or not named_places:
        return []

    resolved_places = _resolve_places(city, named_places, query)
    segments: list[tuple[dict, dict]] = []
    adjacent_pairs = list(zip(resolved_places, resolved_places[1:]))
    all_pairs = [
        (start, end)
        for start in resolved_places
        for end in resolved_places
        if start is not end
    ]
    pairs = adjacent_pairs if not complete_matrix else [*adjacent_pairs, *all_pairs]
    seen_pairs: set[tuple[str, str]] = set()
    for start, end in pairs:
        start_key = _place_identity(start)
        end_key = _place_identity(end)
        # Do not invent a route for an unresolved landmark. AMap's route
        # geometry is only trustworthy after both endpoints are location
        # resolved in the requested city.
        pair_key = (start_key, end_key)
        if (
            start_key == end_key
            or pair_key in seen_pairs
            or not start.get("location")
            or not end.get("location")
        ):
            continue
        seen_pairs.add(pair_key)
        segments.append((start, end))
    return segments


def _select_route_candidate(candidates: list[RoutePlan], query: TravelQuery) -> RoutePlan:
    def duration(item: RoutePlan) -> int:
        return item.duration_minutes if item.duration_minutes is not None else _parse_duration_minutes(item.duration)

    def distance(item: RoutePlan) -> int:
        return item.distance_meters if item.distance_meters is not None else _parse_distance_meters(item.distance)

    def cost(item: RoutePlan) -> tuple[int, float]:
        if item.estimated_cost is None:
            return (1, float("inf"))
        return (0, item.estimated_cost)

    if query.objective == "cheapest":
        return min(candidates, key=lambda item: (cost(item), duration(item)))
    if query.objective == "least_walking":
        return min(
            candidates,
            key=lambda item: (
                0 if item.mode == "walking" else 1,
                distance(item),
                duration(item),
            ),
        )
    return min(candidates, key=lambda item: duration(item))


def _as_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _route_modes() -> tuple[str, ...]:
    modes: list[str] = []
    for item in os.getenv("ROUTE_MATRIX_MODES", "transit,driving,walking").split(","):
        mode = item.strip()
        if mode in {"transit", "driving", "walking"} and mode not in modes:
            modes.append(mode)
    return tuple(modes) or ("driving",)


def _request_budget(max_requests: int | None) -> int:
    if max_requests is not None:
        return max(0, int(max_requests))
    try:
        return max(1, int(os.getenv("ROUTE_MATRIX_MAX_REQUESTS", "60")))
    except ValueError:
        return 60


def _concurrency(complete_matrix: bool) -> int:
    if not complete_matrix:
        return 1
    try:
        return max(1, min(3, int(os.getenv("ROUTE_MATRIX_CONCURRENCY", "3"))))
    except ValueError:
        return 3


def get_route_plans(
    query: TravelQuery,
    *,
    complete_matrix: bool = False,
    max_requests: int | None = None,
    departure_bucket: str | None = None,
) -> RoutePlansResult:
    segments = (
        _build_segments(query, complete_matrix=True)
        if complete_matrix
        else _build_segments(query)
    )
    if not segments:
        return RoutePlansResult()

    modes = _route_modes()
    request_budget = _request_budget(max_requests)
    # Complete-matrix work is deliberately ordered in two phases.  The first
    # mode for every adjacent user edge is attempted before supplementary
    # edges; this leaves a real fallback chain even when the request budget is
    # too small for every mode.
    adjacent_count = min(max(0, len(query.named_places) - 1), len(segments)) if complete_matrix else len(segments)
    adjacent_indexes = list(range(adjacent_count))
    extra_indexes = list(range(adjacent_count, len(segments)))
    ordered_indexes: list[tuple[int, str]] = []
    for indexes in (adjacent_indexes, extra_indexes):
        for mode in modes:
            ordered_indexes.extend((index, mode) for index in indexes)
    tasks = [
        (pair_index, segments[pair_index][0], segments[pair_index][1], mode)
        for pair_index, mode in ordered_indexes[:request_budget]
    ]
    errors: list[str] = []
    attempts = len(tasks)
    by_pair: dict[int, list[RoutePlan]] = {}
    pair_errors: dict[int, list[str]] = {}
    cache_hits = 0
    coalesced_requests = 0
    bucket = str(departure_bucket or query.start_date or query.date or "static").strip() or "static"
    city = (
        query.destination_cities[0]
        if query.destination_scope == "province" and query.destination_cities
        else query.destination or query.city
    )

    def fetch(task: tuple[int, dict, dict, str]):
        pair_index, start, end, mode = task
        try:
            payload, cache_status = _cached_plan_route(
                start["location"],
                end["location"],
                strategy=mode,
                city=city,
                departure_bucket=bucket,
            )
        except Exception as exc:
            return pair_index, start, end, mode, None, f"{mode}:{type(exc).__name__}", "miss"
        return pair_index, start, end, mode, payload, "", cache_status

    with ThreadPoolExecutor(max_workers=_concurrency(complete_matrix), thread_name_prefix="route-matrix") as pool:
        futures = [pool.submit(fetch, task) for task in tasks]
        fetched = [future.result() for future in as_completed(futures)]

    for pair_index, start, end, mode, payload, error, cache_status in sorted(
        fetched, key=lambda item: (item[0], item[3])
    ):
        if cache_status == "cache":
            cache_hits += 1
        elif cache_status == "coalesced":
            coalesced_requests += 1
        if error:
            errors.append(error)
            pair_errors.setdefault(pair_index, []).append(error)
            continue
        if not isinstance(payload, dict) or not payload:
            empty_error = f"{mode}:empty"
            errors.append(empty_error)
            pair_errors.setdefault(pair_index, []).append(empty_error)
            continue
        origin_id = _place_identity(start)
        destination_id = _place_identity(end)
        by_pair.setdefault(pair_index, []).append(
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
                duration_minutes=_as_int(payload.get("duration_minutes"))
                or _parse_duration_minutes(payload.get("duration", "")),
                distance_meters=_as_int(payload.get("distance_meters"))
                or _parse_distance_meters(payload.get("distance", "")),
                estimated_cost=_as_float(payload.get("estimated_cost")),
                cost_currency=str(payload.get("cost_currency") or ""),
                cost_scope=str(payload.get("cost_scope") or ""),
                origin_place_id=origin_id,
                destination_place_id=destination_id,
                source_ids=[_route_source_id(origin_id, destination_id, mode, bucket)],
                departure_bucket=bucket,
            )
        )

    results: list[RoutePlan] = []
    missing_edges: list[tuple[str, str]] = []
    missing_edge_reasons: dict[tuple[str, str], str] = {}
    alternatives: dict[tuple[str, str], list[RoutePlan]] = {}
    for pair_index, (start, end) in enumerate(segments):
        candidates = by_pair.get(pair_index, [])
        edge_key = (_place_identity(start), _place_identity(end))
        if candidates:
            alternatives[edge_key] = list(candidates)
            results.append(_select_route_candidate(candidates, query))
        else:
            display_edge = (start.get("name", ""), end.get("name", ""))
            missing_edges.append(display_edge)
            missing_edge_reasons[display_edge] = (
                "budget_exhausted"
                if pair_index not in {task[0] for task in tasks}
                else "provider_error"
                if pair_errors.get(pair_index)
                else "provider_empty"
            )

    # When a budget truncates a matrix, preserve a partial result and expose
    # exactly which pairs were not queried; never invent geometry.
    expected_requests = len(segments) * len(modes)
    complete = not missing_edges and attempts >= expected_requests and not errors
    if attempts < expected_requests:
        errors.append(f"route_matrix_budget:{attempts}/{expected_requests}")
    return RoutePlansResult(
        results,
        errors=errors,
        complete=complete,
        attempted_requests=attempts,
        missing_edges=missing_edges,
        missing_edge_reasons=missing_edge_reasons,
        alternatives=alternatives,
        cache_hits=cache_hits,
        coalesced_requests=coalesced_requests,
    )


def get_adjacent_route_plans(query: TravelQuery) -> RoutePlansResult:
    """Backward-compatible adjacent-segment lookup for generic city trips."""

    return get_route_plans(query, complete_matrix=False)
