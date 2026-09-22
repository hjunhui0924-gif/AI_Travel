"""Flight provider selection for the travel agent.

The production domestic-flight path is the official Tuniu MCP service,
invoked through the Tuniu CLI. VariFlight remains available only when it is
explicitly selected for an existing deployment; no provider is selected from
credentials implicitly. The former local bridge, HTTP probe, and demo data
paths are intentionally gone.
"""

from __future__ import annotations

import os

from adapters.tuniu_flight_adapter import search_tuniu_flights
from adapters.variflight_adapter import (
    is_variflight_configured,
    search_variflight_flights,
)


def _flight_mcp_mode() -> str:
    """Return the explicitly selected flight provider mode."""

    return os.getenv("FLIGHT_MCP_MODE", "").strip().lower()


def is_flight_mcp_enabled() -> bool:
    enabled = os.getenv("FLIGHT_MCP_ENABLED", "").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return False
    mode = _flight_mcp_mode()
    if mode == "variflight":
        return is_variflight_configured()
    if mode == "tuniu":
        # OAuth credentials live in the CLI profile, so an explicit Tuniu
        # mode is enough to attempt the call and surface an actionable login
        # error instead of silently converting auth failure into empty data.
        return True
    return False


def search_flights(origin: str, destination: str, date: str) -> list[dict]:
    if not is_flight_mcp_enabled():
        return []

    mode = _flight_mcp_mode()
    if mode == "tuniu":
        return search_tuniu_flights(origin, destination, date)
    if mode == "variflight":
        return search_variflight_flights(origin, destination, date)

    raise RuntimeError(f"Unsupported FLIGHT_MCP_MODE: {mode}")
