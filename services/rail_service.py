from __future__ import annotations

from adapters.rail_12306_adapter import query_left_tickets
from agents.schemas import TransportOption, TravelQuery
from services.transport_dates import resolve_transport_dates


class RailOptionsResult(list[TransportOption]):
    """List-compatible rail result with malformed-row metadata."""

    def __init__(self, items: list[TransportOption] | None = None, *, errors: list[str] | None = None):
        super().__init__(items or [])
        self.errors = list(errors or [])

    @property
    def partial(self) -> bool:
        return bool(self.errors)


def _availability_rank(item: dict) -> tuple[int, str]:
    second_class = str(item.get("second_class", "--"))
    no_seat = str(item.get("no_seat", "--"))
    unavailable_tokens = {"--", "", "无", "0"}
    has_ticket = second_class not in unavailable_tokens or no_seat not in unavailable_tokens
    return (0 if has_ticket else 1, item.get("depart_time", "99:99"))


def get_rail_options(query: TravelQuery) -> RailOptionsResult:
    if not query.origin or not query.destination or not query.date:
        return RailOptionsResult()

    provider_options = query_left_tickets(query.date, query.origin, query.destination) or []
    if not isinstance(provider_options, (list, tuple)):
        provider_options = []
    raw_options = [item for item in provider_options if isinstance(item, dict)]

    errors = [] if len(raw_options) == len(provider_options) else ["malformed provider row"]
    results = RailOptionsResult(errors=errors)
    for item in sorted(raw_options, key=_availability_rank)[:5]:
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
                    price="12306 实时票面",
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
