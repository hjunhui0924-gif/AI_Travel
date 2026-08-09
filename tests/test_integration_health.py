from types import SimpleNamespace

from services import integration_health


def test_health_check_is_non_live_by_default_and_does_not_call_network(monkeypatch):
    monkeypatch.delenv("AMAP_WEB_API_KEY", raising=False)
    monkeypatch.delenv("FLIGHT_MCP_ENABLED", raising=False)
    monkeypatch.delenv("FLIGHT_MCP_MODE", raising=False)

    checks = integration_health.run_integration_health_checks(
        live=False,
        only={"amap", "rail_12306", "flight_mcp"},
    )

    statuses = {check.provider: check.status for check in checks}
    assert statuses == {
        "amap": "not_requested",
        "rail_12306": "not_requested",
        "flight_mcp": "not_requested",
    }


def test_live_health_check_exposes_ctrip_block(monkeypatch):
    monkeypatch.setattr(
        integration_health,
        "probe_ctrip_flight_page",
        lambda *args: SimpleNamespace(
            ok=False,
            blocked=True,
            message="Ctrip H5 blocked the request (whaleguard)",
            http_status=432,
            failure_kind="blocked",
            url="https://m.ctrip.com/html5/flight/sha-hgh-day-8.html",
        ),
    )

    checks = integration_health.run_integration_health_checks(live=True, only={"ctrip_h5"})

    assert len(checks) == 1
    assert checks[0].status == "blocked"
    assert checks[0].details["http_status"] == 432


def test_live_health_check_reports_variflight_results(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        integration_health,
        "search_variflight_flights",
        lambda *args: [{"flight_no": "MU6549"}, {"flight_no": "CZ3969"}],
    )

    checks = integration_health.run_integration_health_checks(live=True, only={"variflight"})

    assert len(checks) == 1
    assert checks[0].provider == "variflight"
    assert checks[0].status == "success"
    assert checks[0].details["count"] == 2


def test_health_check_does_not_query_variflight_twice_for_flight_mcp_alias(monkeypatch):
    calls = []
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setenv("FLIGHT_MCP_MODE", "variflight")

    def fake_search(*args):
        calls.append(args)
        return [{"flight_no": "MU6549", "seat_count": 10}]

    monkeypatch.setattr(integration_health, "search_variflight_flights", fake_search)

    checks = integration_health.run_integration_health_checks(
        live=True,
        only={"variflight", "flight_mcp"},
    )

    assert len(calls) == 1
    assert {check.provider: check.status for check in checks} == {
        "variflight": "success",
        "flight_mcp": "alias",
    }
