from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

try:
    from flight_ticket_mcp_server.utils.cities_dict import get_airport_code as _external_get_airport_code
except Exception:
    _external_get_airport_code = None


MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
    "Mobile/15E148 Safari/604.1"
)
STATE_PATTERN = re.compile(r"window\.__INITIAL_STATE__=(\{.*?\});", re.S)


@dataclass(slots=True)
class CtripFlightProbeResult:
    ok: bool
    blocked: bool
    html_excerpt: str = ""
    url: str = ""
    title: str = ""
    message: str = ""
    http_status: int = 0
    failure_kind: str = ""


class CtripFlightError(RuntimeError):
    """A non-bookable Ctrip H5 response, usually caused by anti-bot controls."""

    def __init__(self, message: str, *, failure_kind: str = "failed", http_status: int = 0):
        self.failure_kind = failure_kind
        self.http_status = http_status
        super().__init__(message)


def _detect_block_reason(html: str, status_code: int) -> str:
    lowered = (html or "").lower()
    if "whaleguard" in lowered:
        return "whaleguard"
    if "captcha" in lowered or "\u9a8c\u8bc1\u7801" in html or "\u5b89\u5168\u9a8c\u8bc1" in html:
        return "captcha"
    if status_code >= 400:
        return f"http_{status_code}"
    return ""


def _build_h5_route_url(origin_code: str, destination_code: str, date_text: str) -> str:
    target = datetime.strptime(date_text, "%Y-%m-%d").date()
    today = date.today()
    day_offset = max(1, (target - today).days + 1)
    return (
        "https://m.ctrip.com/html5/flight/"
        f"{origin_code.lower()}-{destination_code.lower()}-day-{day_offset}.html"
    )


def _normalize_airport_code(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if re.fullmatch(r"[A-Za-z]{3}", cleaned):
        return cleaned.upper()
    if _external_get_airport_code:
        code = _external_get_airport_code(cleaned)
        if code:
            return code.upper()
    return cleaned.upper()


def _mobile_headers() -> dict[str, str]:
    return {
        "User-Agent": MOBILE_USER_AGENT,
        "Referer": "https://m.ctrip.com/html5/flight/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _extract_state(html: str) -> dict[str, Any]:
    match = STATE_PATTERN.search(html)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def _format_duration(minutes: int | None) -> str:
    if not minutes:
        return ""
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h{mins:02d}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _duration_minutes(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _price_number(value: Any) -> int:
    text = str(value or "").strip()
    match = re.search(r"\d+", text)
    if not match:
        return 10**9
    return int(match.group(0))


def _time_to_minutes(value: str) -> int:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return 10**9
    return int(match.group(1)) * 60 + int(match.group(2))


def _format_port(port: dict[str, Any]) -> str:
    if not isinstance(port, dict):
        return ""
    pieces = [port.get("name", ""), port.get("terminal", "")]
    return "".join(piece for piece in pieces if piece)


def _normalize_h5_flight(flight: dict[str, Any]) -> dict[str, Any] | None:
    flight_item = flight.get("flightItem") or {}
    segments = flight_item.get("flights") or []
    policy = flight.get("policy") or {}
    if not segments:
        return None

    first = segments[0]
    airline = (first.get("airline") or {}).get("name", "")
    flight_no = first.get("flightNo") or flight.get("dfltnoFlgno") or "Flight"
    depart_time = flight.get("cddate") or ""
    arrive_time = flight.get("cadate") or ""
    duration = _format_duration(flight_item.get("duration") or first.get("duration"))
    price_value = flight.get("showPrice") or flight.get("price") or policy.get("price") or ""
    display_class = flight.get("displayClass") or policy.get("className") or ""
    depart_port = _format_port(first.get("dport") or {})
    arrive_port = _format_port(first.get("aport") or {})
    discount = flight.get("drate") or ""
    stop_count = len(first.get("stops") or [])
    arrive_city_code = ((first.get("aport") or {}).get("cityCode") or "").upper()
    depart_city_code = ((first.get("dport") or {}).get("cityCode") or "").upper()
    segment_count = len(segments)
    duration_minutes = _duration_minutes(flight_item.get("duration") or first.get("duration"))

    summary_parts = []
    if discount:
        summary_parts.append(discount)
    if stop_count:
        summary_parts.append(f"{stop_count}次经停")
    if policy.get("jumpUrl"):
        summary_parts.append("携程H5航班页")

    airport_label = " -> ".join(part for part in [depart_port, arrive_port] if part)

    return {
        "flight_no": flight_no,
        "depart_time": depart_time,
        "arrive_time": arrive_time,
        "duration": duration,
        "price": str(price_value),
        "summary": " | ".join(summary_parts),
        "provider": "CtripH5",
        "airline": airline,
        "cabin": display_class,
        "airport": airport_label,
        "depart_city_code": depart_city_code,
        "arrive_city_code": arrive_city_code,
        "segment_count": segment_count,
        "stop_count": stop_count,
        "duration_minutes": duration_minutes,
    }


def _is_reasonable_h5_flight(item: dict[str, Any], destination_code: str) -> bool:
    if not item:
        return False

    target_code = _normalize_airport_code(destination_code)
    arrive_city_code = str(item.get("arrive_city_code") or "").upper()
    if arrive_city_code != target_code:
        return False

    segment_count = int(item.get("segment_count") or 0)
    if segment_count > 1:
        return False

    duration_minutes = int(item.get("duration_minutes") or 0)
    if duration_minutes and duration_minutes > 360:
        return False

    return True


def _departure_convenience_bucket(item: dict[str, Any]) -> int:
    depart_minutes = _time_to_minutes(str(item.get("depart_time") or ""))
    arrive_minutes = _time_to_minutes(str(item.get("arrive_time") or ""))
    if depart_minutes == 10**9:
        return 3
    if arrive_minutes != 10**9 and arrive_minutes < depart_minutes:
        return 2
    if depart_minutes < 7 * 60 or depart_minutes >= 21 * 60:
        return 1
    return 0


def _flight_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, str, str]:
    return (
        int(item.get("stop_count") or 0),
        _departure_convenience_bucket(item),
        _price_number(item.get("price")),
        int(item.get("duration_minutes") or 10**9),
        str(item.get("depart_time") or ""),
        str(item.get("flight_no") or ""),
    )


def _dedupe_equivalent_flights(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in items:
        key = (
            str(item.get("depart_time") or ""),
            str(item.get("arrive_time") or ""),
            str(item.get("airport") or ""),
            str(item.get("cabin") or ""),
        )
        current = deduped.get(key)
        if current is None or _flight_sort_key(item) < _flight_sort_key(current):
            deduped[key] = item
    return sorted(deduped.values(), key=_flight_sort_key)


def search_ctrip_h5_flights(origin_code: str, destination_code: str, date_text: str) -> list[dict[str, Any]]:
    normalized_origin = _normalize_airport_code(origin_code)
    normalized_destination = _normalize_airport_code(destination_code)
    url = _build_h5_route_url(normalized_origin, normalized_destination, date_text)
    response = requests.get(url, headers=_mobile_headers(), timeout=30)
    blocked_reason = _detect_block_reason(response.text, response.status_code)
    if blocked_reason in {"whaleguard", "captcha"}:
        raise CtripFlightError(
            f"Ctrip H5 blocked the request ({blocked_reason})",
            failure_kind="blocked",
            http_status=response.status_code,
        )
    response.raise_for_status()

    state = _extract_state(response.text)
    if not state:
        raise CtripFlightError(
            "Ctrip H5 response does not contain the expected flight state",
            failure_kind="schema_changed",
            http_status=response.status_code,
        )
    list_data = state.get("listData") or {}
    flights = list_data.get("flights") or []

    normalized: list[dict[str, Any]] = []
    for item in flights:
        if not isinstance(item, dict):
            continue
        normalized_item = _normalize_h5_flight(item)
        if normalized_item and _is_reasonable_h5_flight(normalized_item, normalized_destination):
            normalized.append(normalized_item)
    return _dedupe_equivalent_flights(normalized)


def probe_ctrip_flight_page(origin_code: str, destination_code: str, date_text: str) -> CtripFlightProbeResult:
    normalized_origin = _normalize_airport_code(origin_code)
    normalized_destination = _normalize_airport_code(destination_code)
    url = _build_h5_route_url(normalized_origin, normalized_destination, date_text)
    try:
        response = requests.get(url, headers=_mobile_headers(), timeout=30)
        html = response.text
        blocked_reason = _detect_block_reason(html, response.status_code)
        state = _extract_state(html)
        flights = ((state.get("listData") or {}).get("flights")) or []
        blocked = blocked_reason in {"whaleguard", "captcha"}
        failure_kind = ""
        if blocked:
            failure_kind = "blocked"
        elif response.status_code >= 400:
            failure_kind = blocked_reason or "http_error"
        elif not state:
            failure_kind = "schema_changed"
        elif not flights:
            failure_kind = "empty"
        message = (
            f"parsed {len(flights)} flights from ctrip h5"
            if flights
            else f"ctrip h5 returned no flights ({failure_kind or 'unknown'})"
        )
        return CtripFlightProbeResult(
            ok=bool(flights),
            blocked=blocked,
            html_excerpt=html[:2000],
            url=response.url,
            title="Ctrip H5 Flight Page",
            message=message,
            http_status=response.status_code,
            failure_kind=failure_kind,
        )
    except Exception as exc:
        if isinstance(exc, CtripFlightError):
            return CtripFlightProbeResult(
                ok=False,
                blocked=exc.failure_kind == "blocked",
                url=url,
                title="Ctrip H5 Flight Page",
                message=str(exc),
                http_status=exc.http_status,
                failure_kind=exc.failure_kind,
            )
        return CtripFlightProbeResult(
            ok=False,
            blocked=False,
            message=str(exc),
            url=url,
            failure_kind="network_error",
        )
