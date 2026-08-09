from __future__ import annotations

from adapters.flight_mcp_adapter import is_flight_mcp_enabled, search_flights
from agents.schemas import TransportOption, TravelQuery
from services.transport_dates import resolve_transport_dates


class FlightOptionsResult(list[TransportOption]):
    """List-compatible flight result with malformed-row metadata."""

    def __init__(self, items: list[TransportOption] | None = None, *, errors: list[str] | None = None):
        super().__init__(items or [])
        self.errors = list(errors or [])

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


def _select_recommended_flights(raw_options: list[dict], limit: int = 5) -> list[dict]:
    candidates = _dedupe_by_flight_signature(raw_options)
    if len(candidates) <= limit:
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
        if len(selected) >= limit:
            break
        add_item(item)

    return sorted(selected[:limit], key=_display_priority)


def get_flight_options(query: TravelQuery) -> FlightOptionsResult:
    if not query.origin or not query.destination or not query.date:
        return FlightOptionsResult()

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
    results = FlightOptionsResult(errors=errors)
    for item in _select_recommended_flights(raw_options, limit=5):
        try:
            depart_date, arrive_date = resolve_transport_dates(query.date, item)
            summary_parts = [item.get("summary", "")]
            if item.get("airport"):
                summary_parts.append(item["airport"])

            results.append(
                TransportOption(
                    mode="flight",
                    title=item.get("flight_no", "航班"),
                    depart_time=item.get("depart_time", ""),
                    arrive_time=item.get("arrive_time", ""),
                    duration=item.get("duration", ""),
                    price=item.get("price", ""),
                    summary=" | ".join(part for part in summary_parts if part),
                    provider=item.get("provider", "FlightTicketMCP" if is_flight_mcp_enabled() else ""),
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
