from __future__ import annotations

from adapters.rail_12306_adapter import query_left_tickets
from agents.schemas import TransportOption, TravelQuery


def _availability_rank(item: dict) -> tuple[int, str]:
    second_class = str(item.get("second_class", "--"))
    no_seat = str(item.get("no_seat", "--"))
    unavailable_tokens = {"--", "", "无", "0"}
    has_ticket = second_class not in unavailable_tokens or no_seat not in unavailable_tokens
    return (0 if has_ticket else 1, item.get("depart_time", "99:99"))


def get_rail_options(query: TravelQuery) -> list[TransportOption]:
    if not query.origin or not query.destination or not query.date:
        return []

    try:
        raw_options = query_left_tickets(query.date, query.origin, query.destination)
    except Exception:
        return []

    results: list[TransportOption] = []
    for item in sorted(raw_options, key=_availability_rank)[:5]:
        seats = [
            f"商务座: {item['business_seat']}",
            f"一等座: {item['first_class']}",
            f"二等座: {item['second_class']}",
            f"无座: {item['no_seat']}",
        ]
        results.append(
            TransportOption(
                mode="rail",
                title=item["train_no"],
                depart_time=item["depart_time"],
                arrive_time=item["arrive_time"],
                duration=item["duration"],
                price="12306 实时票面",
                summary=f"{item['from_station']} -> {item['to_station']}",
                provider="12306",
                seats=seats,
            )
        )
    return results
