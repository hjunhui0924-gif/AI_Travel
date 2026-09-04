from __future__ import annotations

import re

from adapters.flight_mcp_adapter import search_flights
from agents.schemas import TransportOption, TravelQuery
from services.transport_dates import is_valid_clock_time, resolve_transport_dates


DEFAULT_TRANSPORT_PAGE_SIZE = 5
MAX_TRANSPORT_PAGE_SIZE = 20


class FlightOptionsResult(list[TransportOption]):
    """List-compatible flight result with malformed-row metadata."""

    def __init__(
        self,
        items: list[TransportOption] | None = None,
        *,
        errors: list[str] | None = None,
        offset: int = 0,
        limit: int = DEFAULT_TRANSPORT_PAGE_SIZE,
        total_count: int = 0,
    ):
        super().__init__(items or [])
        self.errors = list(errors or [])
        self.offset = max(0, offset)
        self.limit = max(1, limit)
        self.total_count = max(0, total_count)

    @property
    def has_more(self) -> bool:
        return self.offset + len(self) < self.total_count

    @property
    def partial(self) -> bool:
        return bool(self.errors)


def _time_to_minutes(value: str) -> int:
    text = (value or "").strip()
    if ":" not in text:
        return 10**9
    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except ValueError:
        return 10**9


def _is_valid_flight_no(value: object) -> bool:
    text = str(value or "").strip().upper()
    if text in {"FLIGHT", "航班"}:
        return False
    return bool(re.fullmatch(r"[A-Z0-9-]{2,12}", text)) and any(char.isalpha() for char in text)


def _is_valid_flight_row(item: dict) -> bool:
    return (
        _is_valid_flight_no(item.get("flight_no"))
        and is_valid_clock_time(item.get("depart_time"))
        and is_valid_clock_time(item.get("arrive_time"))
        and bool(str(item.get("provider") or "").strip())
    )


def _price_to_int(value: str) -> int:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else 10**9


def _selection_key(item: dict) -> tuple[int, int, int, str]:
    return (
        _price_to_int(str(item.get("price", ""))),
        _time_to_minutes(str(item.get("depart_time", ""))),
        _time_to_minutes(str(item.get("arrive_time", ""))),
        str(item.get("flight_no", "")),
    )


def _display_priority(item: dict) -> tuple[int, int, int, str]:
    depart_minutes = _time_to_minutes(str(item.get("depart_time", "")))
    if depart_minutes == 10**9:
        bucket = 4
    elif depart_minutes < 8 * 60:
        bucket = 3
    elif depart_minutes < 12 * 60:
        bucket = 1
    elif depart_minutes < 19 * 60:
        bucket = 0
    elif depart_minutes < 22 * 60:
        bucket = 2
    else:
        bucket = 4
    return (
        bucket,
        _price_to_int(str(item.get("price", ""))),
        depart_minutes,
        str(item.get("flight_no", "")),
    )


def _dedupe_by_flight_signature(items: list[dict]) -> list[dict]:
    deduped: dict[tuple[str, str, str], dict] = {}
    for item in items:
        key = (
            str(item.get("depart_time", "")),
            str(item.get("arrive_time", "")),
            str(item.get("airport", "")),
        )
        current = deduped.get(key)
        if current is None or _selection_key(item) < _selection_key(current):
            deduped[key] = item
    return list(deduped.values())


def _rank_recommended_flights(raw_options: list[dict]) -> list[dict]:
    candidates = _dedupe_by_flight_signature(raw_options)
    if len(candidates) <= DEFAULT_TRANSPORT_PAGE_SIZE:
        return sorted(candidates, key=_selection_key)

    buckets = [
        ("morning", 6 * 60, 12 * 60),
        ("afternoon", 12 * 60, 18 * 60),
        ("evening", 18 * 60, 24 * 60),
    ]

    selected: list[dict] = []
    selected_keys: set[tuple[str, str, str]] = set()

    def add_item(item: dict) -> None:
        key = (
            str(item.get("depart_time", "")),
            str(item.get("arrive_time", "")),
            str(item.get("airport", "")),
        )
        if key in selected_keys:
            return
        selected.append(item)
        selected_keys.add(key)

    sorted_candidates = sorted(candidates, key=_selection_key)
    for _name, start_minute, end_minute in buckets:
        bucket_items = [
            item
            for item in sorted_candidates
            if start_minute <= _time_to_minutes(str(item.get("depart_time", ""))) < end_minute
        ]
        if bucket_items:
            add_item(bucket_items[0])

    for item in sorted_candidates:
        add_item(item)

    return sorted(selected, key=_display_priority)


def _select_recommended_flights(
    raw_options: list[dict],
    limit: int = DEFAULT_TRANSPORT_PAGE_SIZE,
    offset: int = 0,
) -> list[dict]:
    ordered = _rank_recommended_flights(raw_options)
    return ordered[offset : offset + limit]


def get_flight_options(
    query: TravelQuery,
    *,
    offset: int = 0,
    limit: int = DEFAULT_TRANSPORT_PAGE_SIZE,
) -> FlightOptionsResult:
    offset = max(0, int(offset))
    limit = min(MAX_TRANSPORT_PAGE_SIZE, max(1, int(limit)))
    if not query.origin or not query.destination or not query.date:
        return FlightOptionsResult(offset=offset, limit=limit)

    provider_options = search_flights(query.origin, query.destination, query.date)
    if provider_options is None:
        provider_options = []
    provider_errors = list(getattr(provider_options, "errors", []) or [])
    if not isinstance(provider_options, (list, tuple)):
        provider_options = []
    raw_options = [item for item in provider_options if isinstance(item, dict)]

    errors = list(provider_errors)
    if len(raw_options) != len(provider_options):
        errors.append("malformed provider row")
    valid_options = []
    malformed_rows = 0
    for item in raw_options:
        if _is_valid_flight_row(item):
            valid_options.append(item)
        else:
            malformed_rows += 1
    if malformed_rows:
        errors.append("malformed provider row")
    raw_options = valid_options
    ordered_options = _rank_recommended_flights(raw_options)
    total_count = len(ordered_options)
    results = FlightOptionsResult(
        errors=errors,
        offset=offset,
        limit=limit,
        total_count=total_count,
    )
    for item in ordered_options[offset : offset + limit]:
        try:
            depart_date, arrive_date = resolve_transport_dates(query.date, item)
            summary_parts = [item.get("summary", "")]
            if item.get("airport"):
                summary_parts.append(item["airport"])

            results.append(
                TransportOption(
                    mode="flight",
                    title=str(item.get("flight_no") or ""),
                    depart_time=str(item.get("depart_time") or ""),
                    arrive_time=str(item.get("arrive_time") or ""),
                    duration=item.get("duration", ""),
                    price=item.get("price", ""),
                    summary=" | ".join(part for part in summary_parts if part),
                    provider=str(item.get("provider") or ""),
                    seats=[
                        part
                        for part in [
                            item.get("airline", ""),
                            item.get("cabin", ""),
                            item.get("airport", ""),
                        ]
                        if part
                    ],
                    is_demo=bool(item.get("is_demo", False)),
                    depart_date=depart_date,
                    arrive_date=arrive_date,
                    seat_count=item.get("seat_count"),
                )
            )
        except Exception:
            # A malformed provider row must not discard valid flights from
            # the same response.  The orchestrator will expose an empty or
            # partial-looking result rather than inventing a replacement.
            results.errors.append(f"row:{type(item).__name__}")
            continue
    return results
