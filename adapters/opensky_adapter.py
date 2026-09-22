"""OpenSky live aircraft-state adapter.

OpenSky exposes ADS-B state vectors, not future schedules, fares, or seat
inventory.  This adapter therefore returns live aircraft observations only and
keeps that limitation explicit in the domain type and provider diagnostics.
"""

from __future__ import annotations

import os
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from agents.schemas import OpenSkyFlightStatus


DEFAULT_URL = "https://opensky-network.org/api/states/all"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RETRIES = 1
DEFAULT_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"


class OpenSkyError(RuntimeError):
    def __init__(self, message: str, *, failure_kind: str = "failed", http_status: int = 0):
        self.failure_kind = failure_kind
        self.http_status = http_status
        super().__init__(message)


class OpenSkyResults(list[OpenSkyFlightStatus]):
    def __init__(self, items: list[OpenSkyFlightStatus] | None = None, *, errors: list[str] | None = None):
        super().__init__(items or [])
        self.errors = list(errors or [])


_token_lock = threading.RLock()
_token_value = ""
_token_expires_at = 0.0


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(max(float(os.getenv(name, str(default))), minimum), maximum)
    except ValueError:
        return default


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(max(int(os.getenv(name, str(default))), minimum), maximum)
    except ValueError:
        return default


def _bounds() -> dict[str, float]:
    # Mainland China plus nearby airspace.  The values are configurable so a
    # deployment can narrow the request and reduce anonymous API rate pressure.
    return {
        "lamin": _env_float("OPENSKY_LAT_MIN", 18.0, -90.0, 90.0),
        "lomin": _env_float("OPENSKY_LON_MIN", 73.0, -180.0, 180.0),
        "lamax": _env_float("OPENSKY_LAT_MAX", 54.0, -90.0, 90.0),
        "lomax": _env_float("OPENSKY_LON_MAX", 135.0, -180.0, 180.0),
    }


def is_opensky_configured() -> bool:
    return True


def _credentials() -> tuple[str, str]:
    client_id = os.getenv("OPENSKY_CLIENT_ID", "").strip()
    client_secret = os.getenv("OPENSKY_CLIENT_SECRET", "").strip()
    credentials_file = os.getenv("OPENSKY_CREDENTIALS_FILE", "").strip()
    if credentials_file and (not client_id or not client_secret):
        try:
            with open(credentials_file, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if isinstance(payload, dict):
                client_id = client_id or str(payload.get("clientId") or payload.get("client_id") or "").strip()
                client_secret = client_secret or str(payload.get("clientSecret") or payload.get("client_secret") or "").strip()
        except (OSError, ValueError):
            pass
    return client_id, client_secret


def _access_token() -> str:
    global _token_value, _token_expires_at
    client_id, client_secret = _credentials()
    if not client_id or not client_secret:
        return ""
    now = time.monotonic()
    with _token_lock:
        if _token_value and now < _token_expires_at - 30:
            return _token_value
        timeout = _env_int("OPENSKY_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 1, 60)
        try:
            response = requests.post(
                os.getenv("OPENSKY_TOKEN_URL", DEFAULT_TOKEN_URL),
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise OpenSkyError(
                f"OpenSky OAuth request failed: {type(exc).__name__}",
                failure_kind="network",
            ) from exc
        if response.status_code >= 400:
            raise OpenSkyError(
                f"OpenSky OAuth request failed: HTTP {response.status_code}",
                failure_kind="unauthorized" if response.status_code in {401, 403} else "http_error",
                http_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenSkyError("OpenSky OAuth returned invalid JSON", failure_kind="schema_changed") from exc
        token = str(payload.get("access_token") or "") if isinstance(payload, dict) else ""
        if not token:
            raise OpenSkyError("OpenSky OAuth response has no access_token", failure_kind="schema_changed")
        _token_value = token
        _token_expires_at = now + float(payload.get("expires_in") or 1800)
        return token


def _iso_timestamp(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_from_state(state: list[Any], observed_at: str) -> OpenSkyFlightStatus | None:
    if not isinstance(state, list) or len(state) < 17:
        return None
    icao24 = str(state[0] or "").strip().lower()
    if not icao24:
        return None
    callsign = str(state[1] or "").strip()
    return OpenSkyFlightStatus(
        icao24=icao24,
        callsign=callsign,
        origin_country=str(state[2] or "").strip(),
        observed_at=observed_at,
        last_contact_at=_iso_timestamp(state[4]),
        longitude=_number(state[5]),
        latitude=_number(state[6]),
        baro_altitude_m=_number(state[7]),
        on_ground=bool(state[8]) if state[8] is not None else None,
        velocity_mps=_number(state[9]),
        heading_deg=_number(state[10]),
        vertical_rate_mps=_number(state[11]),
        geo_altitude_m=_number(state[13]),
        source_id=f"opensky_{icao24}_{int(float(state[4] or 0)) if state[4] else 0}",
    )


def search_opensky_states() -> OpenSkyResults:
    url = os.getenv("OPENSKY_API_URL", DEFAULT_URL).strip() or DEFAULT_URL
    timeout = _env_int("OPENSKY_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 1, 60)
    max_retries = _env_int("OPENSKY_MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, 1)
    headers = {"User-Agent": "AI-Travel-Agent/1.0"}
    token = _access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, params=_bounds(), headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise OpenSkyError(f"OpenSky request failed: {type(exc).__name__}", failure_kind="network") from exc
        if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries:
            time.sleep(0.25 * (attempt + 1))
            continue
        if response.status_code >= 400:
            raise OpenSkyError(
                f"OpenSky request failed: HTTP {response.status_code}",
                failure_kind="rate_limit" if response.status_code == 429 else "http_error",
                http_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenSkyError("OpenSky returned invalid JSON", failure_kind="schema_changed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("states"), list):
            raise OpenSkyError("OpenSky response has no state list", failure_kind="schema_changed")
        observed_at = _iso_timestamp(payload.get("time"))
        result = [
            item
            for item in (_status_from_state(state, observed_at) for state in payload["states"])
            if item is not None and item.callsign
        ]
        return OpenSkyResults(result)
    raise OpenSkyError("OpenSky request exhausted retries", failure_kind="network") from last_error
