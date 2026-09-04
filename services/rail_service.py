from __future__ import annotations

import re

from adapters.rail_12306_adapter import query_left_tickets
from agents.schemas import TransportOption, TravelQuery
from services.transport_dates import is_valid_clock_time, resolve_transport_dates


HIGH_SPEED_TRAIN_PREFIXES = ("G", "D", "C")
DEFAULT_TRANSPORT_PAGE_SIZE = 5
MAX_TRANSPORT_PAGE_SIZE = 20


def _wants_high_speed(query: TravelQuery) -> bool:
    text = str(query.raw_text or "")
    return any(token in text for token in ("高铁", "动车", "G字头", "D字头", "C字头"))


def _is_high_speed_train(train_no: object) -> bool:
    value = str(train_no or "").strip().upper()
    return bool(value) and value.startswith(HIGH_SPEED_TRAIN_PREFIXES)


def _is_valid_train_no(value: object) -> bool:
    text = str(value or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z0-9]{1,8}", text)) and any(char.isdigit() for char in text)


def _is_valid_rail_row(item: dict) -> bool:
    return (
        _is_valid_train_no(item.get("train_no"))
        and is_valid_clock_time(item.get("depart_time"))
        and is_valid_clock_time(item.get("arrive_time"))
    )


class RailOptionsResult(list[TransportOption]):
    """List-compatible rail result with malformed-row metadata."""

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


def _availability_rank(item: dict) -> tuple[int, str]:
    second_class = str(item.get("second_class", "--"))
    no_seat = str(item.get("no_seat", "--"))
    unavailable_tokens = {"--", "", "无", "0"}
    has_ticket = second_class not in unavailable_tokens or no_seat not in unavailable_tokens
    return (0 if has_ticket else 1, item.get("depart_time", "99:99"))


def get_rail_options(
    query: TravelQuery,
    *,
    offset: int = 0,
    limit: int = DEFAULT_TRANSPORT_PAGE_SIZE,
) -> RailOptionsResult:
    offset = max(0, int(offset))
    limit = min(MAX_TRANSPORT_PAGE_SIZE, max(1, int(limit)))
    if not query.origin or not query.destination or not query.date:
        return RailOptionsResult(offset=offset, limit=limit)

    provider_options = query_left_tickets(query.date, query.origin, query.destination) or []
    if not isinstance(provider_options, (list, tuple)):
        provider_options = []
    raw_options = [item for item in provider_options if isinstance(item, dict)]
    malformed_count = len(raw_options) != len(provider_options)
    if _wants_high_speed(query):
        raw_options = [item for item in raw_options if _is_high_speed_train(item.get("train_no"))]

    valid_options = []
    for item in raw_options:
        if _is_valid_rail_row(item):
            valid_options.append(item)
        else:
            malformed_count = True
    raw_options = valid_options

    errors = ["malformed provider row"] if malformed_count else []
    ordered_options = sorted(raw_options, key=_availability_rank)
    total_count = len(ordered_options)
    results = RailOptionsResult(
        errors=errors,
        offset=offset,
        limit=limit,
        total_count=total_count,
    )
    for item in ordered_options[offset : offset + limit]:
        try:
            depart_date, arrive_date = resolve_transport_dates(query.date, item)
            seats = [
                f"商务座: {item.get('business_seat', '--')}",
                f"一等座: {item.get('first_class', '--')}",
                f"二等座: {item.get('second_class', '--')}",
                f"无座: {item.get('no_seat', '--')}",
            ]
            results.append(
                TransportOption(
                    mode="rail",
                    title=str(item.get("train_no") or "车次"),
                    depart_time=str(item.get("depart_time") or ""),
                    arrive_time=str(item.get("arrive_time") or ""),
                    duration=str(item.get("duration") or ""),
                    # The left-ticket response contains seat availability but
                    # not a reliable fare. Keep price empty so the UI cannot
                    # mistake provider text for a currency amount.
                    price="",
                    summary=f"{item.get('from_station', query.origin)} -> {item.get('to_station', query.destination)}",
                    provider="12306",
                    seats=seats,
                    depart_date=depart_date,
                    arrive_date=arrive_date,
                )
            )
        except Exception:
            # One malformed provider row must not discard the other options.
            results.errors.append(f"row:{type(item).__name__}")
            continue
    return results
