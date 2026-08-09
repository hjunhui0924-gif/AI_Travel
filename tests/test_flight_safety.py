import pytest
import subprocess

from adapters import ctrip_flight_adapter, flight_mcp_adapter
from bridges import flight_mcp_bridge


def test_flight_mcp_is_off_without_explicit_configuration(monkeypatch):
    monkeypatch.delenv("FLIGHT_MCP_ENABLED", raising=False)
    monkeypatch.delenv("FLIGHT_MCP_MODE", raising=False)

    assert flight_mcp_adapter.is_flight_mcp_enabled() is False
    assert flight_mcp_adapter.search_flights("上海", "杭州", "2026-09-02") == []


def test_auto_bridge_does_not_invent_demo_flight(monkeypatch):
    monkeypatch.setattr(flight_mcp_bridge, "_search_via_installed_package", lambda *args: [])
    monkeypatch.setattr(flight_mcp_bridge, "search_ctrip_h5_flights", lambda *args: [])

    assert flight_mcp_bridge._search_via_auto("SHA", "HGH", "2026-09-02") == []


def test_ctrip_probe_classifies_whaleguard_block(monkeypatch):
    class FakeResponse:
        status_code = 432
        text = "whaleguard block\n"
        url = "https://m.ctrip.com/html5/flight/sha-hgh-day-8.html"

    monkeypatch.setattr(ctrip_flight_adapter.requests, "get", lambda *args, **kwargs: FakeResponse())

    result = ctrip_flight_adapter.probe_ctrip_flight_page("SHA", "HGH", "2026-08-16")

    assert result.ok is False
    assert result.blocked is True
    assert result.http_status == 432
    assert result.failure_kind == "blocked"

    with pytest.raises(ctrip_flight_adapter.CtripFlightError):
        ctrip_flight_adapter.search_ctrip_h5_flights("SHA", "HGH", "2026-08-16")


def test_auto_bridge_surfaces_provider_failure(monkeypatch):
    monkeypatch.setattr(
        flight_mcp_bridge,
        "_search_via_installed_package",
        lambda *args: (_ for _ in ()).throw(RuntimeError("package unavailable")),
    )
    monkeypatch.setattr(
        flight_mcp_bridge,
        "search_ctrip_h5_flights",
        lambda *args: (_ for _ in ()).throw(RuntimeError("ctrip blocked")),
    )

    with pytest.raises(flight_mcp_bridge.FlightBridgeError) as exc_info:
        flight_mcp_bridge._search_via_auto("SHA", "HGH", "2026-09-02")

    assert "package unavailable" in str(exc_info.value)
    assert "ctrip blocked" in str(exc_info.value)


def test_flight_command_timeout_is_reported_and_process_tree_is_terminated(monkeypatch):
    class FakeProcess:
        pid = 12345
        returncode = None

        def communicate(self, **kwargs):
            raise subprocess.TimeoutExpired("flight-command", 1)

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9

    terminated = []
    monkeypatch.setenv("FLIGHT_MCP_COMMAND", "flight-command")
    monkeypatch.setenv("FLIGHT_MCP_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(flight_mcp_adapter.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        flight_mcp_adapter,
        "_terminate_process_tree",
        lambda process: terminated.append(process.pid),
    )

    with pytest.raises(flight_mcp_adapter.FlightQueryError, match="timed out"):
        flight_mcp_adapter._search_flights_via_command("SHA", "HGH", "2026-09-02")

    assert terminated == [12345]
