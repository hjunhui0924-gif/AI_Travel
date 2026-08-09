from adapters import flight_mcp_adapter
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

