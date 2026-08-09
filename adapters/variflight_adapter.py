"""Authorized VariFlight flight-price adapter.

The public interface is intentionally small: callers provide an origin,
destination, and ISO date and receive normalized, non-demo flight candidates.
VariFlight's price endpoint accepts city IATA codes, so this adapter also
contains the airport-to-city normalization and the provider-specific response
handling needed by the rest of the travel planner.

This adapter never falls back to web pages or fabricated flights.  An empty
``data`` list is a real empty result; authentication, protocol, and network
failures are raised as :class:`VariFlightError` so the planner can expose a
failed/partial provider state.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_API_URL = "https://mcp.variflight.com/api/v1/mcp/data"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5


class VariFlightError(RuntimeError):
    """A configured VariFlight request failed without a safe result."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: str = "failed",
        http_status: int = 0,
        provider_code: str = "",
    ):
        self.failure_kind = failure_kind
        self.http_status = http_status
        self.provider_code = provider_code
        super().__init__(message)


class VariFlightResults(list[dict[str, Any]]):
    """List-compatible results carrying non-fatal provider row diagnostics."""

    def __init__(self, items: list[dict[str, Any]] | None = None, *, errors: list[str] | None = None):
        super().__init__(items or [])
        self.errors = list(errors or [])


# VariFlight's pricing tool expects city codes.  Keep common airport aliases
# here so user input such as PEK/PVG can be used safely without pretending that
# an airport code is always a city code (PEK and PVG are the important cases).
CITY_CODE_ALIASES = {
    "北京": "BJS",
    "北京市": "BJS",
    "北京首都": "BJS",
    "北京大兴": "BJS",
    "首都机场": "BJS",
    "大兴机场": "BJS",
    "PEK": "BJS",
    "PKX": "BJS",
    "NAY": "BJS",
    "上海": "SHA",
    "上海市": "SHA",
    "上海浦东": "SHA",
    "上海虹桥": "SHA",
    "浦东机场": "SHA",
    "虹桥机场": "SHA",
    "PVG": "SHA",
    "SHA": "SHA",
    "广州": "CAN",
    "广州市": "CAN",
    "广州白云": "CAN",
    "白云机场": "CAN",
    "CAN": "CAN",
    "深圳": "SZX",
    "深圳市": "SZX",
    "深圳宝安": "SZX",
    "宝安机场": "SZX",
    "SZX": "SZX",
    "杭州": "HGH",
    "杭州市": "HGH",
    "杭州萧山": "HGH",
    "萧山机场": "HGH",
    "HGH": "HGH",
    "成都": "CTU",
    "成都市": "CTU",
    "成都双流": "CTU",
    "成都天府": "CTU",
    "双流机场": "CTU",
    "天府机场": "CTU",
    "CTU": "CTU",
    "TFU": "CTU",
    "重庆": "CKG",
    "重庆市": "CKG",
    "重庆江北": "CKG",
    "江北机场": "CKG",
    "CKG": "CKG",
    "南京": "NKG",
    "南京市": "NKG",
    "南京禄口": "NKG",
    "禄口机场": "NKG",
    "NKG": "NKG",
    "武汉": "WUH",
    "武汉市": "WUH",
    "武汉天河": "WUH",
    "天河机场": "WUH",
    "WUH": "WUH",
    "厦门": "XMN",
    "厦门市": "XMN",
    "厦门高崎": "XMN",
    "高崎机场": "XMN",
    "XMN": "XMN",
    "西安": "SIA",
    "西安市": "SIA",
    "西安咸阳": "SIA",
    "咸阳机场": "SIA",
    "XIY": "SIA",
    "SIA": "SIA",
    "长沙": "CSX",
    "长沙市": "CSX",
    "长沙黄花": "CSX",
    "黄花机场": "CSX",
    "CSX": "CSX",
    "昆明": "KMG",
    "昆明市": "KMG",
    "昆明长水": "KMG",
    "长水机场": "KMG",
    "KMG": "KMG",
    "郑州": "CGO",
    "郑州市": "CGO",
    "郑州新郑": "CGO",
    "新郑机场": "CGO",
    "CGO": "CGO",
    "青岛": "TAO",
    "青岛市": "TAO",
    "青岛胶东": "TAO",
    "胶东机场": "TAO",
    "TAO": "TAO",
    "大连": "DLC",
    "大连市": "DLC",
    "大连周水子": "DLC",
    "周水子机场": "DLC",
    "DLC": "DLC",
    "三亚": "SYX",
    "三亚市": "SYX",
    "三亚凤凰": "SYX",
    "凤凰机场": "SYX",
    "SYX": "SYX",
    "海口": "HAK",
    "海口市": "HAK",
    "海口美兰": "HAK",
    "美兰机场": "HAK",
    "HAK": "HAK",
    "天津": "TSN",
    "天津市": "TSN",
    "天津滨海": "TSN",
    "滨海机场": "TSN",
    "TSN": "TSN",
    "福州": "FOC",
    "福州市": "FOC",
    "福州长乐": "FOC",
    "长乐机场": "FOC",
    "FOC": "FOC",
    "济南": "TNA",
    "济南市": "TNA",
    "济南遥墙": "TNA",
    "遥墙机场": "TNA",
    "TNA": "TNA",
    "合肥": "HFE",
    "合肥市": "HFE",
    "合肥新桥": "HFE",
    "新桥机场": "HFE",
    "HFE": "HFE",
    "贵阳": "KWE",
    "贵阳市": "KWE",
    "贵阳龙洞堡": "KWE",
    "龙洞堡机场": "KWE",
    "KWE": "KWE",
    "南宁": "NNG",
    "南宁市": "NNG",
    "南宁吴圩": "NNG",
    "吴圩机场": "NNG",
    "NNG": "NNG",
    "哈尔滨": "HRB",
    "哈尔滨市": "HRB",
    "哈尔滨太平": "HRB",
    "太平机场": "HRB",
    "HRB": "HRB",
    "沈阳": "SHE",
    "沈阳市": "SHE",
    "沈阳桃仙": "SHE",
    "桃仙机场": "SHE",
    "SHE": "SHE",
    "乌鲁木齐": "URC",
    "乌鲁木齐市": "URC",
    "乌鲁木齐地窝堡": "URC",
    "地窝堡机场": "URC",
    "URC": "URC",
    "宁波": "NGB",
    "宁波市": "NGB",
    "宁波栎社": "NGB",
    "栎社机场": "NGB",
    "NGB": "NGB",
    "石家庄": "SJW",
    "石家庄市": "SJW",
    "石家庄正定": "SJW",
    "正定机场": "SJW",
    "SJW": "SJW",
}


def _configured_city_aliases() -> dict[str, str]:
    """Load operator-supplied city/airport aliases without trusting user input."""

    raw = os.getenv("VARIFLIGHT_CITY_CODE_ALIASES_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VariFlightError(
            "invalid VariFlight city-code alias configuration",
            failure_kind="invalid_config",
        ) from exc
    if not isinstance(payload, dict):
        raise VariFlightError(
            "VariFlight city-code aliases must be a JSON object",
            failure_kind="invalid_config",
        )

    aliases: dict[str, str] = {}
    for alias, code in payload.items():
        alias_text = str(alias or "").strip()
        code_text = str(code or "").strip().upper()
        if alias_text.isascii():
            alias_text = alias_text.upper()
        if not alias_text or not re.fullmatch(r"[A-Z]{3}", code_text):
            raise VariFlightError(
                "VariFlight city-code aliases must map to three-letter codes",
                failure_kind="invalid_config",
            )
        aliases[alias_text] = code_text
    return aliases


def is_variflight_configured() -> bool:
    """Return whether the provider has an API key and a valid HTTP URL."""

    api_key = os.getenv("VARIFLIGHT_API_KEY", "").strip()
    api_url = os.getenv("VARIFLIGHT_API_URL", DEFAULT_API_URL).strip()
    parsed = urlparse(api_url)
    return bool(api_key and parsed.scheme in {"http", "https"} and parsed.netloc)


def _city_code(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise VariFlightError("origin or destination is empty", failure_kind="invalid_input")

    aliases = dict(CITY_CODE_ALIASES)
    aliases.update(_configured_city_aliases())
    compact = text.replace(" ", "").upper()
    if compact in aliases:
        return aliases[compact]

    original = text.replace(" ", "")
    if original in aliases:
        return aliases[original]
    for alias, code in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if any("\u4e00" <= char <= "\u9fff" for char in alias) and alias in original:
            return code

    if re.fullmatch(r"[A-Za-z]{3}", text):
        code = text.upper()
        if code in set(aliases.values()):
            return code
        raise VariFlightError(
            f"unsupported airport or city code: {text}; configure its city mapping",
            failure_kind="invalid_input",
        )
    raise VariFlightError(
        f"unsupported city or airport code: {text}",
        failure_kind="invalid_input",
    )


def _iso_date(value: str) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise VariFlightError(
            f"invalid departure date: {text}",
            failure_kind="invalid_input",
        ) from exc


def _configured_request_settings() -> tuple[str, str, int, int, float]:
    api_key = os.getenv("VARIFLIGHT_API_KEY", "").strip()
    api_url = os.getenv("VARIFLIGHT_API_URL", DEFAULT_API_URL).strip()
    parsed = urlparse(api_url)
    if not api_key or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VariFlightError("VariFlight is not configured", failure_kind="not_configured")

    try:
        timeout_seconds = max(1, int(os.getenv("VARIFLIGHT_HTTP_TIMEOUT_SECONDS", "20")))
        max_retries = max(0, int(os.getenv("VARIFLIGHT_MAX_RETRIES", "1")))
        retry_backoff = max(
            0.0,
            float(os.getenv("VARIFLIGHT_RETRY_BACKOFF_SECONDS", "0.5")),
        )
    except ValueError as exc:
        raise VariFlightError(
            "invalid VariFlight HTTP tuning configuration",
            failure_kind="invalid_config",
        ) from exc
    return api_key, api_url, timeout_seconds, max_retries, retry_backoff


def _request_payload(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    api_key, api_url, timeout_seconds, max_retries, retry_backoff = _configured_request_settings()
    request_body = {"endpoint": endpoint, "params": params}
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                api_url,
                headers={
                    "X-VARIFLIGHT-KEY": api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=request_body,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(retry_backoff * (attempt + 1))
                continue
            raise VariFlightError(
                f"VariFlight request failed: {type(exc).__name__}",
                failure_kind="network",
            ) from exc

        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < max_retries:
            time.sleep(retry_backoff * (attempt + 1))
            continue
        if response.status_code in {401, 403}:
            raise VariFlightError(
                f"VariFlight authorization failed: HTTP {response.status_code}",
                failure_kind="unauthorized",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise VariFlightError(
                f"VariFlight HTTP error: {response.status_code}",
                failure_kind="http_error",
                http_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise VariFlightError(
                "VariFlight returned invalid JSON",
                failure_kind="schema_changed",
                http_status=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise VariFlightError(
                "VariFlight returned a non-object payload",
                failure_kind="schema_changed",
                http_status=response.status_code,
            )

        provider_code = str(payload.get("code") or "")
        if provider_code and provider_code not in {"0", "200"}:
            message = str(payload.get("message") or payload.get("msg") or "provider error")
            raise VariFlightError(
                f"VariFlight {provider_code}: {message[:300]}",
                failure_kind="provider_error",
                http_status=response.status_code,
                provider_code=provider_code,
            )
        return payload

    raise VariFlightError(
        f"VariFlight request failed: {type(last_error).__name__ if last_error else 'unknown'}",
        failure_kind="network",
    )


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    """Return the first provider field that is present, including numeric zero."""

    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _valid_date_hint(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return fallback


def _parse_datetime(value: Any, date_hint: str = "") -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10**11:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, CN_TZ)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if re.fullmatch(r"\d{10,13}", text):
        return _parse_datetime(int(text), date_hint)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M", "%H%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.startswith("%H"):
            try:
                parsed = parsed.replace(year=date.fromisoformat(date_hint).year,
                                        month=date.fromisoformat(date_hint).month,
                                        day=date.fromisoformat(date_hint).day)
            except ValueError:
                return None
        return parsed.replace(tzinfo=CN_TZ)
    return None


def _format_duration(departure: datetime | None, arrival: datetime | None) -> str:
    if departure is None or arrival is None:
        return ""
    if arrival < departure:
        arrival += timedelta(days=1)
    minutes = max(0, round((arrival - departure).total_seconds() / 60))
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}h{remainder:02d}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _label(name: Any, code: Any, terminal: Any = "") -> str:
    name_text = str(name or "").strip()
    code_text = str(code or "").strip().upper()
    terminal_text = str(terminal or "").strip()
    base = f"{name_text}({code_text})" if name_text and code_text else name_text or code_text
    return f"{base} {terminal_text}".strip()


def _pick_cabin(cabins: Any) -> tuple[dict[str, Any], float, int | None] | None:
    if not isinstance(cabins, list):
        return None
    candidates: list[tuple[dict[str, Any], float, int | None]] = []
    for cabin in cabins:
        if not isinstance(cabin, dict):
            continue
        price = _as_number(
            _first_present(cabin, "price", "saleprice", "salePrice", "showPrice", "showprice")
        )
        if price is None or price <= 0:
            continue
        seat_count = _as_int(_first_present(cabin, "seatnum", "seat_num", "seatNum", "seats"))
        # A known zero means the fare is not currently sellable.  Unknown
        # availability is retained but must remain distinguishable upstream.
        if seat_count is not None and seat_count <= 0:
            continue
        candidates.append((cabin, price, seat_count))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1], item[2] is None))


def _looks_like_flight_row(item: dict[str, Any]) -> bool:
    """Distinguish a schema row from a valid flight with zero inventory."""

    flight_no = str(item.get("flightno") or item.get("flight_no") or "").strip()
    cabins = item.get("cabins")
    if not flight_no or not isinstance(cabins, list) or not cabins:
        return False
    return any(
        isinstance(cabin, dict)
        and (_as_number(_first_present(cabin, "price", "saleprice", "salePrice", "showPrice", "showprice")) or 0) > 0
        for cabin in cabins
    )


def _normalize_flight(
    item: dict[str, Any],
    origin: str,
    destination: str,
    default_departure_date: str = "",
) -> dict[str, Any] | None:
    selected = _pick_cabin(item.get("cabins"))
    if selected is None:
        return None
    cabin, price, seat_count = selected

    depart_date_hint = _valid_date_hint(
        _first_present(item, "depdate", "departure_date"),
        default_departure_date,
    )
    arrive_date_hint = _valid_date_hint(
        _first_present(item, "arrdate", "arrival_date"),
        depart_date_hint,
    )
    departure = _parse_datetime(
        item.get("flightdeptimeplandate") or item.get("departure_time") or item.get("deptime"),
        depart_date_hint,
    )
    arrival = _parse_datetime(
        item.get("flightarrtimeplandate") or item.get("arrival_time") or item.get("arrtime"),
        arrive_date_hint,
    )
    # Provider payloads sometimes contain only clock times or report an
    # arrival timestamp on the departure date.  A backward clock means an
    # overnight flight for this endpoint; normalize it once so both the
    # duration and the calendar date remain consistent.
    if departure is not None and arrival is not None and arrival < departure:
        arrival += timedelta(days=1)
    depart_date = departure.date().isoformat() if departure else depart_date_hint
    arrive_date = arrival.date().isoformat() if arrival else arrive_date_hint
    departure_label = _label(item.get("depaptcname"), item.get("flightdepcode"), item.get("flighthterminal"))
    arrival_label = _label(item.get("arraptcname"), item.get("flightarrcode"), item.get("flightterminal"))
    route_label = " -> ".join(
        part
        for part in (
            _label(item.get("depaptcname"), item.get("flightdepcode")),
            _label(item.get("arraptcname"), item.get("flightarrcode")),
        )
        if part
    )
    airport_label = " -> ".join(part for part in (departure_label, arrival_label) if part)

    return {
        "flight_no": str(item.get("flightno") or item.get("flight_no") or "").strip(),
        "origin": str(item.get("depcitycode") or origin).upper(),
        "destination": str(item.get("arrcitycode") or destination).upper(),
        "depart_time": departure.strftime("%H:%M") if departure else "",
        "arrive_time": arrival.strftime("%H:%M") if arrival else "",
        "depart_date": depart_date,
        "arrive_date": arrive_date,
        "duration": _format_duration(departure, arrival),
        "price": str(int(price) if price.is_integer() else price),
        "summary": route_label,
        "provider": "VariFlight",
        "airline": str(item.get("flightcompany") or item.get("airline") or "").strip(),
        "cabin": str(
            _first_present(cabin, "classname", "cabinclass", "cabinName") or ""
        ).strip(),
        "airport": airport_label,
        "seat_count": seat_count,
        "is_demo": False,
    }


def search_variflight_flights(origin: str, destination: str, date_text: str) -> VariFlightResults:
    """Search current sale-flight candidates by route and departure date."""

    departure_date = _iso_date(date_text)
    origin_city = _city_code(origin)
    destination_city = _city_code(destination)
    payload = _request_payload(
        "getFlightPriceByCities",
        {
            "dep_city": origin_city,
            "arr_city": destination_city,
            "dep_date": departure_date,
            "price_mode": "lowest",
        },
    )
    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        for key in ("flights", "items", "results"):
            if isinstance(payload.get(key), list):
                raw_items = payload[key]
                break
    if not isinstance(raw_items, list):
        raise VariFlightError(
            "VariFlight response has no flight list",
            failure_kind="schema_changed",
        )

    normalized = []
    malformed_rows = 0
    for item in raw_items:
        if not isinstance(item, dict):
            malformed_rows += 1
            continue
        if not _looks_like_flight_row(item):
            malformed_rows += 1
            continue
        result = _normalize_flight(item, origin_city, destination_city, departure_date)
        if result and result["flight_no"]:
            normalized.append(result)
    if raw_items and malformed_rows == len(raw_items):
        raise VariFlightError(
            "VariFlight flight rows have an unsupported structure",
            failure_kind="schema_changed",
        )

    ordered = sorted(
        normalized,
        key=lambda item: (
            float(item["price"]) if str(item.get("price", "")).replace(".", "", 1).isdigit() else 10**9,
            item.get("depart_date", ""),
            item.get("depart_time", ""),
            item.get("flight_no", ""),
        ),
    )
    if malformed_rows:
        return VariFlightResults(
            ordered,
            errors=[f"provider_rows_schema_changed:{malformed_rows}"],
        )
    return VariFlightResults(ordered)
