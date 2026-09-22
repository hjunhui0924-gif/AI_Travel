"""Amadeus Flight Offers adapter.

This is an official OAuth/API integration, unlike the optional browser-scraping
flight package.  The test endpoint is useful for wiring and schema checks;
production availability requires an Amadeus account and production base URL.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_BASE_URL = "https://test.api.amadeus.com"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RETRIES = 1


class AmadeusError(RuntimeError):
    def __init__(self, message: str, *, failure_kind: str = "failed", http_status: int = 0, provider_code: str = ""):
        self.failure_kind = failure_kind
        self.http_status = http_status
        self.provider_code = provider_code
        super().__init__(message)


class AmadeusResults(list[dict[str, Any]]):
    def __init__(self, items: list[dict[str, Any]] | None = None, *, errors: list[str] | None = None):
        super().__init__(items or [])
        self.errors = list(errors or [])


CITY_CODE_ALIASES = {
    "北京": "BJS", "北京市": "BJS", "北京首都": "PEK", "北京大兴": "PKX",
    "上海": "SHA", "上海市": "SHA", "上海虹桥": "SHA", "上海浦东": "PVG",
    "广州": "CAN", "广州市": "CAN", "深圳": "SZX", "深圳市": "SZX",
    "杭州": "HGH", "杭州市": "HGH", "成都": "CTU", "成都市": "CTU",
    "重庆": "CKG", "重庆市": "CKG", "西安": "XIY", "西安市": "XIY",
    "武汉": "WUH", "武汉市": "WUH", "厦门": "XMN", "厦门市": "XMN",
    "南京": "NKG", "南京市": "NKG", "青岛": "TAO", "青岛市": "TAO",
    "长沙": "CSX", "长沙市": "CSX", "昆明": "KMG", "昆明市": "KMG",
    "海口": "HAK", "三亚": "SYX", "三亚市": "SYX", "福州": "FOC",
    "济南": "TNA", "郑州": "CGO", "天津": "TSN", "大连": "DLC",
    "宁波": "NGB", "合肥": "HFE", "贵阳": "KWE", "南昌": "KHN",
    "南宁": "NNG", "石家庄": "SJW", "太原": "TYN", "乌鲁木齐": "URC",
    "哈尔滨": "HRB", "沈阳": "SHE", "兰州": "LHW", "呼和浩特": "HET",
    "桂林": "KWL", "温州": "WNZ", "烟台": "YNT", "珠海": "ZUH",
    "徐州": "XUZ", "扬州": "YTY", "无锡": "WUX", "泉州": "JJN",
}
_token_lock = threading.RLock()
_token_value = ""
_token_expires_at = 0.0


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 120) -> int:
    try:
        return min(max(int(os.getenv(name, str(default))), minimum), maximum)
    except ValueError:
        return default


def _settings() -> tuple[str, str, str, int, int]:
    client_id = os.getenv("AMADEUS_CLIENT_ID", "").strip()
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET", "").strip()
    base_url = os.getenv("AMADEUS_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(base_url)
    if not client_id or not client_secret:
        raise AmadeusError("Amadeus credentials are not configured", failure_kind="not_configured")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AmadeusError("invalid AMADEUS_BASE_URL", failure_kind="invalid_config")
    return (
        client_id,
        client_secret,
        base_url,
        _env_int("AMADEUS_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        _env_int("AMADEUS_MAX_RETRIES", DEFAULT_MAX_RETRIES, minimum=0, maximum=1),
    )


def is_amadeus_configured() -> bool:
    try:
        _settings()
    except AmadeusError:
        return False
    return True


def _city_code(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AmadeusError("origin or destination is empty", failure_kind="invalid_input")
    aliases = dict(CITY_CODE_ALIASES)
    raw_aliases = os.getenv("AMADEUS_CITY_CODE_ALIASES_JSON", "").strip()
    if raw_aliases:
        try:
            configured = json.loads(raw_aliases)
        except json.JSONDecodeError as exc:
            raise AmadeusError("invalid AMADEUS_CITY_CODE_ALIASES_JSON", failure_kind="invalid_config") from exc
        if not isinstance(configured, dict):
            raise AmadeusError("AMADEUS_CITY_CODE_ALIASES_JSON must be an object", failure_kind="invalid_config")
        aliases.update({str(key).strip(): str(code).strip().upper() for key, code in configured.items()})
    compact = text.replace(" ", "")
    if compact in aliases:
        return aliases[compact]
    if re.fullmatch(r"[A-Za-z]{3}", compact):
        return compact.upper()
    raise AmadeusError(f"unsupported city or airport: {text}", failure_kind="invalid_input")


def _request_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AmadeusError("Amadeus returned invalid JSON", failure_kind="schema_changed", http_status=response.status_code) from exc
    if not isinstance(payload, dict):
        raise AmadeusError("Amadeus returned a non-object payload", failure_kind="schema_changed", http_status=response.status_code)
    return payload


def _access_token(force_refresh: bool = False) -> str:
    global _token_value, _token_expires_at
    client_id, client_secret, base_url, timeout, max_retries = _settings()
    now = time.monotonic()
    with _token_lock:
        if not force_refresh and _token_value and now < _token_expires_at - 30:
            return _token_value
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(
                    f"{base_url}/v1/security/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=timeout,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                if response.status_code >= 400:
                    payload = _request_json(response)
                    detail = str(payload.get("error_description") or payload.get("error") or "token request rejected")
                    raise AmadeusError(
                        f"Amadeus token request failed: {detail[:240]}",
                        failure_kind="unauthorized" if response.status_code in {401, 403} else "http_error",
                        http_status=response.status_code,
                        provider_code=str(payload.get("code") or ""),
                    )
                payload = _request_json(response)
                token = str(payload.get("access_token") or "")
                if not token:
                    raise AmadeusError("Amadeus token response has no access_token", failure_kind="schema_changed")
                _token_value = token
                _token_expires_at = now + float(payload.get("expires_in") or 1800)
                return token
            except AmadeusError:
                raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise AmadeusError(f"Amadeus token request failed: {type(exc).__name__}", failure_kind="network") from exc
        raise AmadeusError("Amadeus token request failed", failure_kind="network") from last_error


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_duration(value: object) -> str:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", str(value or "").strip().upper())
    if not match:
        return ""
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def _first_segment(offer: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    itineraries = offer.get("itineraries")
    if not isinstance(itineraries, list) or not itineraries:
        return None
    itinerary = itineraries[0]
    segments = itinerary.get("segments") if isinstance(itinerary, dict) else None
    if not isinstance(segments, list) or not segments:
        return None
    first = segments[0]
    last = segments[-1]
    if not isinstance(first, dict) or not isinstance(last, dict):
        return None
    return first, last


def _normalize_offer(offer: dict[str, Any], origin: str, destination: str) -> dict[str, Any] | None:
    segment_pair = _first_segment(offer)
    if segment_pair is None:
        return None
    first, last = segment_pair
    departure = first.get("departure") if isinstance(first.get("departure"), dict) else {}
    arrival = last.get("arrival") if isinstance(last.get("arrival"), dict) else {}
    depart_at = _parse_datetime(departure.get("at"))
    arrive_at = _parse_datetime(arrival.get("at"))
    carrier = str(first.get("carrierCode") or "").strip().upper()
    number = str(first.get("number") or "").strip()
    if not depart_at or not arrive_at or not carrier or not number:
        return None
    price = offer.get("price") if isinstance(offer.get("price"), dict) else {}
    fare_details = offer.get("travelerPricings")
    cabin = ""
    if isinstance(fare_details, list) and fare_details and isinstance(fare_details[0], dict):
        details = fare_details[0].get("fareDetailsBySegment")
        if isinstance(details, list) and details and isinstance(details[0], dict):
            cabin = str(details[0].get("cabin") or "")
    seats = offer.get("numberOfBookableSeats")
    try:
        seat_count = int(seats) if seats is not None else None
    except (TypeError, ValueError):
        seat_count = None
    dep_code = str(departure.get("iataCode") or "").upper()
    arr_code = str(arrival.get("iataCode") or "").upper()
    return {
        "flight_no": f"{carrier}{number}",
        "origin": origin,
        "destination": destination,
        "depart_time": depart_at.strftime("%H:%M"),
        "arrive_time": arrive_at.strftime("%H:%M"),
        "depart_date": depart_at.date().isoformat(),
        "arrive_date": arrive_at.date().isoformat(),
        "duration": _format_duration((offer.get("itineraries") or [{}])[0].get("duration")),
        "price": f"{price.get('currency', '')} {price.get('total', '')}".strip(),
        "summary": f"{dep_code} -> {arr_code}".strip(" ->"),
        "provider": "Amadeus",
        "airline": carrier,
        "cabin": cabin,
        "airport": f"{dep_code} -> {arr_code}".strip(" ->"),
        "seat_count": seat_count,
        "is_demo": False,
    }


def search_amadeus_flights(origin: str, destination: str, date: str) -> AmadeusResults:
    origin_code = _city_code(origin)
    destination_code = _city_code(destination)
    try:
        datetime.fromisoformat(str(date))
    except ValueError as exc:
        raise AmadeusError(f"invalid departure date: {date}", failure_kind="invalid_input") from exc
    _, _, base_url, timeout, max_retries = _settings()
    payload = {
        "originLocationCode": origin_code,
        "destinationLocationCode": destination_code,
        "departureDate": str(date),
        "adults": "1",
        "max": "20",
        "currencyCode": os.getenv("AMADEUS_CURRENCY_CODE", "CNY").strip() or "CNY",
    }
    errors: list[str] = []
    for attempt in range(max_retries + 1):
        token = _access_token()
        try:
            response = requests.get(
                f"{base_url}/v2/shopping/flight-offers",
                headers={"Authorization": f"Bearer {token}"},
                params=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt < max_retries:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise AmadeusError(f"Amadeus flight request failed: {type(exc).__name__}", failure_kind="network") from exc
        if response.status_code == 401 and attempt < max_retries:
            with _token_lock:
                global _token_value, _token_expires_at
                _token_value = ""
                _token_expires_at = 0.0
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries:
            time.sleep(0.2 * (attempt + 1))
            continue
        if response.status_code >= 400:
            result = _request_json(response)
            errors_payload = result.get("errors") if isinstance(result.get("errors"), list) else []
            first_error = errors_payload[0] if errors_payload and isinstance(errors_payload[0], dict) else {}
            message = str(first_error.get("detail") or first_error.get("title") or result.get("message") or "flight request rejected")
            raise AmadeusError(
                f"Amadeus flight request failed: {message[:240]}",
                failure_kind="unauthorized" if response.status_code in {401, 403} else "http_error",
                http_status=response.status_code,
                provider_code=str(first_error.get("code") or ""),
            )
        result = _request_json(response)
        offers = result.get("data") if isinstance(result.get("data"), list) else []
        normalized = [
            item
            for item in (_normalize_offer(offer, origin, destination) for offer in offers if isinstance(offer, dict))
            if item is not None
        ]
        if offers and not normalized:
            errors.append("provider rows could not be normalized")
        return AmadeusResults(normalized, errors=errors)
    return AmadeusResults(errors=["Amadeus request exhausted retries"])
