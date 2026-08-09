"""Normalize departure/arrival dates for transport adapter results."""

from __future__ import annotations

from datetime import date, timedelta


def _parse_iso_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _time_to_minutes(value: object) -> int | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    hour_text, minute_text = text.split(":", 1)
    try:
        hour = int(hour_text[-2:]) if hour_text[-2:].isdigit() else int(hour_text)
        minute = int(minute_text[:2])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _arrival_day_offset(item: dict) -> int | None:
    for key in ("arrive_day_offset", "arrival_day_offset", "day_offset"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue

    for key in ("next_day", "is_next_day", "arrive_next_day", "arrival_next_day"):
        value = item.get(key)
        if isinstance(value, str):
            value = value.strip().lower() in {"1", "true", "yes", "y", "是"}
        if value:
            return 1
    return None


def resolve_transport_dates(base_date: str, item: dict) -> tuple[str, str]:
    """Return ISO departure and arrival dates for one adapter result.

    Adapters may provide explicit dates or only clock times.  When only times
    exist, an arrival clock earlier than departure is treated as next-day.
    This is a conservative calendar hint, not a claim that the ticket is
    available or bookable.
    """

    fallback = _parse_iso_date(base_date)
    if fallback is None:
        return str(base_date or ""), str(base_date or "")

    depart = _parse_iso_date(
        item.get("depart_date") or item.get("departure_date") or item.get("date")
    ) or fallback
    arrival = _parse_iso_date(
        item.get("arrive_date") or item.get("arrival_date")
    )
    if arrival is None:
        offset = _arrival_day_offset(item)
        if offset is None:
            depart_minutes = _time_to_minutes(item.get("depart_time"))
            arrive_minutes = _time_to_minutes(item.get("arrive_time"))
            offset = 1 if depart_minutes is not None and arrive_minutes is not None and arrive_minutes < depart_minutes else 0
        arrival = depart + timedelta(days=offset)

    if arrival < depart:
        arrival = depart
    return depart.isoformat(), arrival.isoformat()
