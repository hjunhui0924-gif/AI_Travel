from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


class FlightQueryError(RuntimeError):
    pass


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BRIDGE_PATH = BASE_DIR / "bridges" / "flight_mcp_bridge.py"


def _default_bridge_command() -> str:
    if DEFAULT_BRIDGE_PATH.exists():
        return f'"{sys.executable}" "{DEFAULT_BRIDGE_PATH}"'
    return ""


def _flight_mcp_mode() -> str:
    explicit_mode = os.getenv("FLIGHT_MCP_MODE", "").strip().lower()
    if explicit_mode:
        return explicit_mode
    enabled = os.getenv("FLIGHT_MCP_ENABLED", "").strip().lower()
    if enabled in {"1", "true", "yes", "on"} and _default_bridge_command():
        return "auto"
    return ""


def is_flight_mcp_enabled() -> bool:
    return bool(_flight_mcp_mode())


def _normalize_flight_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("flights", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _search_flights_via_command(
    origin: str,
    destination: str,
    date: str,
    bridge_mode: str = "auto",
) -> list[dict]:
    command = os.getenv("FLIGHT_MCP_COMMAND", "").strip() or _default_bridge_command()
    if not command:
        raise FlightQueryError("FLIGHT_MCP_COMMAND is empty")

    timeout_seconds = int(os.getenv("FLIGHT_MCP_TIMEOUT_SECONDS", "120"))
    env = os.environ.copy()
    if bridge_mode:
        env["FLIGHT_BRIDGE_MODE"] = bridge_mode

    completed = subprocess.run(
        command,
        input=json.dumps(
            {
                "origin": origin,
                "destination": destination,
                "date": date,
            },
            ensure_ascii=True,
        ),
        capture_output=True,
        text=True,
        shell=True,
        timeout=timeout_seconds,
        env=env,
    )

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise FlightQueryError(stderr or f"Flight MCP command failed with exit code {completed.returncode}")

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return []

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FlightQueryError(f"Flight MCP returned invalid JSON: {exc}") from exc

    return _normalize_flight_items(payload)


def search_flights(origin: str, destination: str, date: str) -> list[dict]:
    if not is_flight_mcp_enabled():
        return []

    mode = _flight_mcp_mode()
    if mode in {"command", "auto", "package", "http", "dummy", "ctrip_h5"}:
        bridge_mode = "auto" if mode == "command" else mode
        return _search_flights_via_command(origin, destination, date, bridge_mode=bridge_mode)

    raise FlightQueryError(f"Unsupported FLIGHT_MCP_MODE: {mode}")
