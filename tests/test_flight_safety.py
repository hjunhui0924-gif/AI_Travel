import json

import pytest

from adapters import flight_mcp_adapter
from adapters import tuniu_flight_adapter


def test_flight_provider_is_off_without_explicit_configuration(monkeypatch):
    monkeypatch.delenv("FLIGHT_MCP_ENABLED", raising=False)
    monkeypatch.delenv("FLIGHT_MCP_MODE", raising=False)

    assert flight_mcp_adapter.is_flight_mcp_enabled() is False
    assert flight_mcp_adapter.search_flights("上海", "杭州", "2026-09-02") == []


def test_tuniu_mode_is_explicit_and_does_not_use_demo_data(monkeypatch):
    monkeypatch.setenv("FLIGHT_MCP_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_MCP_MODE", "tuniu")
    monkeypatch.setattr(
        flight_mcp_adapter,
        "search_tuniu_flights",
        lambda origin, destination, date: [
            {
                "flight_no": "MU6549",
                "origin": origin,
                "destination": destination,
                "date": date,
                "provider": "Tuniu",
                "is_demo": False,
            }
        ],
    )

    assert flight_mcp_adapter.is_flight_mcp_enabled() is True
    result = flight_mcp_adapter.search_flights("上海", "杭州", "2026-09-02")
    assert result[0]["flight_no"] == "MU6549"
    assert result[0]["is_demo"] is False


def test_tuniu_api_key_is_passed_only_through_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("TUNIU_AUTH_TYPE", "apiKey")
    monkeypatch.setenv("TUNIU_API_KEY", "tn-test-secret")

    class Completed:
        returncode = 0
        stdout = json.dumps({"successCode": True, "data": []})
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Completed()

    monkeypatch.setattr(tuniu_flight_adapter.subprocess, "run", fake_run)
    tuniu_flight_adapter.search_tuniu_flights("北京", "上海", "2026-10-01")

    assert "tn-test-secret" not in " ".join(captured["command"])
    assert captured["env"]["TUNIU_API_KEY"] == "tn-test-secret"


def test_tuniu_provider_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("FLIGHT_MCP_ENABLED", "false")
    monkeypatch.setenv("FLIGHT_MCP_MODE", "tuniu")

    assert flight_mcp_adapter.is_flight_mcp_enabled() is False
    assert flight_mcp_adapter.search_flights("北京", "上海", "2026-10-01") == []


def test_tuniu_adapter_normalizes_documented_search_response(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

        class Completed:
            returncode = 0
            stdout = json.dumps(
                {
                    "success": True,
                    "result": {
                        "successCode": True,
                        "queryId": "query-1",
                        "totalPageNum": 2,
                        "data": [
                            {
                                "flightNumber": "MU5101",
                                "airlineCompany": "东航",
                                "departureTime": "2026-10-01 08:20",
                                "arrivalTime": "2026-10-01 10:35",
                                "departureAirport": "首都T2",
                                "arrivalAirport": "虹桥T2",
                                "basePrice": "680",
                                "totalTax": "50",
                                "cabinClass": "经济舱",
                                "remainingSeats": "9",
                                "totalDuration": "2h15m",
                                "type": "直飞",
                                "craftType": "A320",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
            stderr = ""

        return Completed()

    monkeypatch.setattr(tuniu_flight_adapter.subprocess, "run", fake_run)
    results = tuniu_flight_adapter.search_tuniu_flights("北京", "上海", "2026-10-01")

    assert results.query_id == "query-1"
    assert results.total_page_num == 2
    assert results[0] == {
        "flight_no": "MU5101",
            "origin": "北京",
            "destination": "上海",
        "depart_time": "08:20",
        "arrive_time": "10:35",
        "depart_date": "2026-10-01",
        "arrive_date": "2026-10-01",
        "duration": "2h15m",
        "price": "730",
        "summary": "直飞 | 机型 A320 | 票面 ¥680 + 税费 ¥50",
        "provider": "Tuniu",
        "airline": "东航",
        "cabin": "经济舱",
        "airport": "首都T2 -> 虹桥T2",
        "seat_count": 9,
        "is_demo": False,
    }
    assert captured["command"][0].lower().endswith(("tuniu", "tuniu.cmd", "tuniu.ps1"))
    assert captured["command"][1:3] == ["call", "flight"]
    assert json.loads(captured["command"][5]) == {
        "departureCityName": "北京",
        "arrivalCityName": "上海",
        "departureDate": "2026-10-01",
    }
    assert "saveOrder" not in captured["command"]


def test_tuniu_adapter_preserves_partial_rows_as_diagnostics(monkeypatch):
    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "successCode": True,
                "data": [
                    {
                        "flightNumber": "CZ1",
                        "departureTime": "2026-10-01 08:00",
                        "arrivalTime": "2026-10-01 10:00",
                    },
                    {"flightNumber": "BROKEN"},
                ],
            }
        )
        stderr = ""

    monkeypatch.setattr(tuniu_flight_adapter.subprocess, "run", lambda *args, **kwargs: Completed())
    results = tuniu_flight_adapter.search_tuniu_flights("北京", "上海", "2026-10-01")

    assert [item["flight_no"] for item in results] == ["CZ1"]
    assert results.errors == ["provider_rows_schema_changed:1"]


def test_tuniu_cli_auth_failure_is_not_treated_as_empty(monkeypatch):
    monkeypatch.setenv("TUNIU_AUTH_TYPE", "apiKey")
    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "success": False,
                "error": {
                    "type": "OAuthLoginRequiredError",
                    "code": 110,
                    "message": "请执行 tuniu auth login",
                },
            },
            ensure_ascii=False,
        )
        stderr = ""

    monkeypatch.setattr(tuniu_flight_adapter.subprocess, "run", lambda *args, **kwargs: Completed())

    with pytest.raises(tuniu_flight_adapter.TuniuFlightError) as exc_info:
        tuniu_flight_adapter.search_tuniu_flights("北京", "上海", "2026-10-01")

    assert exc_info.value.failure_kind == "unauthorized"
    assert str(exc_info.value) == "途牛 API Key 未配置或无效，请检查 TUNIU_API_KEY。"


def test_tuniu_does_not_turn_missing_base_price_into_tax_only_price(monkeypatch):
    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "successCode": True,
                "data": [
                    {
                        "flightNumber": "CA1",
                        "departureTime": "2026-10-01 08:00",
                        "arrivalTime": "2026-10-01 10:00",
                        "totalTax": "50",
                        "remainingSeats": "-1",
                    }
                ],
            }
        )
        stderr = ""

    monkeypatch.setattr(tuniu_flight_adapter.subprocess, "run", lambda *args, **kwargs: Completed())
    result = tuniu_flight_adapter.search_tuniu_flights("北京", "上海", "2026-10-01")[0]

    assert result["price"] == ""
    assert result["seat_count"] is None


def test_variflight_mode_remains_explicit_for_legacy_deployments(monkeypatch):
    monkeypatch.setenv("FLIGHT_MCP_MODE", "variflight")
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setenv("VARIFLIGHT_API_URL", "https://example.test/mcp")
    monkeypatch.setattr(
        flight_mcp_adapter,
        "search_variflight_flights",
        lambda origin, destination, date: [{"flight_no": "MU6549"}],
    )

    assert flight_mcp_adapter.search_flights("SHA", "HGH", "2026-08-16")[0]["flight_no"] == "MU6549"
