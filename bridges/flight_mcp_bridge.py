from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from adapters.ctrip_flight_adapter import search_ctrip_h5_flights


class FlightBridgeError(RuntimeError):
    """A configured flight provider failed without a safe result."""


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        raise ValueError("stdin payload is empty")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("stdin payload must be a JSON object")
    return payload


def _normalize_city(value: str) -> str:
    return (value or "").strip()


def _normalize_flight_no(item: dict) -> str:
    return (
        item.get("flight_no")
        or item.get("flightNumber")
        or item.get("flight_number")
        or item.get("航班号")
        or "Flight"
    )


def _normalize_depart_time(item: dict) -> str:
    return (
        item.get("depart_time")
        or item.get("departure_time")
        or item.get("takeoff_time")
        or item.get("出发时间")
        or ""
    )


def _normalize_arrive_time(item: dict) -> str:
    return (
        item.get("arrive_time")
        or item.get("arrival_time")
        or item.get("landing_time")
        or item.get("到达时间")
        or ""
    )


def _normalize_price(item: dict) -> str:
    return str(
        item.get("price")
        or item.get("lowest_price")
        or item.get("价格")
        or item.get("票价")
        or ""
    )


def _normalize_duration(item: dict) -> str:
    return item.get("duration") or item.get("飞行时长") or ""


def _normalize_summary(item: dict) -> str:
    return (
        item.get("summary")
        or item.get("route")
        or item.get("formatted_output")
        or "FlightTicketMCP package result"
    )


def _normalize_provider(item: dict, provider: str) -> str:
    return item.get("provider") or provider


def _normalize_airline(item: dict) -> str:
    return item.get("airline") or item.get("company") or item.get("航空公司") or ""


def _normalize_cabin(item: dict) -> str:
    return item.get("cabin") or item.get("seat_class") or item.get("舱位") or ""


def _normalize_airport(item: dict) -> str:
    airport_parts = [
        item.get("airport", ""),
        item.get("departure_airport", ""),
        item.get("arrival_airport", ""),
        item.get("出发机场", ""),
        item.get("到达机场", ""),
    ]
    return " -> ".join([part for part in airport_parts if part])


def _normalize_flight_item(item: dict, origin: str, destination: str, provider: str) -> dict:
    return {
        "flight_no": _normalize_flight_no(item),
        "origin": origin,
        "destination": destination,
        "depart_time": _normalize_depart_time(item),
        "arrive_time": _normalize_arrive_time(item),
        "duration": _normalize_duration(item),
        "price": _normalize_price(item),
        "summary": _normalize_summary(item),
        "provider": _normalize_provider(item, provider),
        "airline": _normalize_airline(item),
        "cabin": _normalize_cabin(item),
        "airport": _normalize_airport(item),
        "is_demo": bool(item.get("is_demo", False)),
    }


def _dummy_response(origin: str, destination: str, date: str) -> list[dict]:
    return [
        {
            "flight_no": "MU5123",
            "origin": origin,
            "destination": destination,
            "depart_time": f"{date} 09:15",
            "arrive_time": f"{date} 11:05",
            "duration": "1h50m",
            "price": "680",
            "summary": "bridge fallback demo flight",
            "provider": "FlightTicketMCP-bridge-fallback",
            "airline": "China Eastern",
            "cabin": "Economy",
            "is_demo": True,
        }
    ]


def _search_via_http(origin: str, destination: str, date: str) -> list[dict]:
    endpoint = os.getenv("FLIGHT_MCP_HTTP_URL", "").strip()
    if not endpoint:
        return []

    response = requests.post(
        endpoint,
        json={
            "origin": origin,
            "destination": destination,
            "date": date,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("flights", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _search_via_installed_package(origin: str, destination: str, date: str) -> list[dict]:
    from flight_ticket_mcp_server.tools.flight_search_tools import get_airport_code, searchFlightRoutes

    departure_code = get_airport_code(origin) or origin
    destination_code = get_airport_code(destination) or destination

    sink = io.StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        payload = searchFlightRoutes(departure_code, destination_code, date)

    if not isinstance(payload, dict):
        raise FlightBridgeError("FlightTicketMCP returned a non-object payload")
    if payload.get("status") != "success":
        error_code = payload.get("error_code") or "PROVIDER_ERROR"
        message = payload.get("message") or "FlightTicketMCP returned an error"
        raise FlightBridgeError(f"{error_code}: {message}")

    items = []
    for key in ("flights", "items", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            break

    normalized = []
    for item in items:
        normalized.append(_normalize_flight_item(item, origin, destination, "FlightTicketMCP"))
    return normalized


def _search_via_auto(origin: str, destination: str, date: str) -> list[dict]:
    errors: list[str] = []
    try:
        flights = _search_via_installed_package(origin, destination, date)
    except Exception as exc:
        errors.append(f"package:{type(exc).__name__}:{exc}")
        flights = []

    if flights:
        return flights

    try:
        flights = search_ctrip_h5_flights(origin, destination, date)
    except Exception as exc:
        errors.append(f"ctrip_h5:{type(exc).__name__}:{exc}")
        flights = []

    if flights:
        return flights
    if errors:
        raise FlightBridgeError("; ".join(errors))
    # An empty result is safer than inventing a bookable-looking flight.
    return []


def main() -> int:
    try:
        payload = _read_payload()
        origin = _normalize_city(str(payload.get("origin", "")))
        destination = _normalize_city(str(payload.get("destination", "")))
        date = str(payload.get("date", "")).strip()
        if not origin or not destination or not date:
            raise ValueError("origin, destination, and date are required")

        mode = os.getenv("FLIGHT_BRIDGE_MODE", "auto").strip().lower()
        if mode == "http":
            flights = _search_via_http(origin, destination, date)
        elif mode == "package":
            flights = _search_via_installed_package(origin, destination, date)
        elif mode == "ctrip_h5":
            flights = search_ctrip_h5_flights(origin, destination, date)
        elif mode == "auto":
            flights = _search_via_auto(origin, destination, date)
        elif mode == "dummy" and os.getenv("FLIGHT_ALLOW_DEMO_DATA", "").strip().lower() in {"1", "true", "yes", "on"}:
            flights = _dummy_response(origin, destination, date)
        else:
            raise ValueError("unsupported or disabled flight bridge mode")

        sys.stdout.write(json.dumps(flights, ensure_ascii=True))
        return 0
    except Exception as exc:
        error_payload = {
            "error": str(exc),
            "provider": "flight_mcp_bridge",
            "status": "failed",
        }
        print(str(exc), file=sys.stderr)
        sys.stdout.write(json.dumps(error_payload, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
