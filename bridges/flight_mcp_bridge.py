from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from adapters.variflight_adapter import search_variflight_flights


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
            "provider": "FlightBridge-demo",
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


def main() -> int:
    try:
        payload = _read_payload()
        origin = _normalize_city(str(payload.get("origin", "")))
        destination = _normalize_city(str(payload.get("destination", "")))
        date = str(payload.get("date", "")).strip()
        if not origin or not destination or not date:
            raise ValueError("origin, destination, and date are required")

        mode = os.getenv("FLIGHT_BRIDGE_MODE", "http").strip().lower()
        if mode == "http":
            flights = _search_via_http(origin, destination, date)
        elif mode == "variflight":
            flights = search_variflight_flights(origin, destination, date)
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
