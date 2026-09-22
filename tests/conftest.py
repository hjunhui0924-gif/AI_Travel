from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_external_flight_provider_environment(monkeypatch):
    """Unit tests must not inherit the developer's live flight mode from .env."""

    monkeypatch.delenv("FLIGHT_MCP_ENABLED", raising=False)
    monkeypatch.delenv("FLIGHT_MCP_MODE", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OPENSKY_CREDENTIALS_FILE", raising=False)
