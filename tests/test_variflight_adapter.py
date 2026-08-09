from __future__ import annotations

import json

import pytest

from adapters import variflight_adapter


class _FakeResponse:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)
        self.headers = {}

    def json(self):
        return self._payload


def test_search_variflight_flights_normalizes_price_and_seat_data(monkeypatch):
    calls = []
    payload = {
        "code": 200,
        "message": "Success",
        "data": [
            {
                "flightcompany": "MU",
                "flightno": "MU6549",
                "flightdepcode": "PVG",
                "flightarrcode": "HGH",
                "depaptcname": "上海浦东",
                "arraptcname": "杭州萧山",
                "depcitycode": "SHA",
                "arrcitycode": "HGH",
                "flighthterminal": "T1",
                "flightterminal": "T3",
                "flightdeptimeplandate": 1786894200,
                "depdate": "2026-08-16",
                "flightarrtimeplandate": 1786898400,
                "arrdate": "2026-08-17",
                "cabins": [
                    {
                        "cabinclass": "Y",
                        "classname": "经济舱",
                        "seatnum": 10,
                        "price": 230,
                    }
                ],
            }
        ],
    }

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(200, payload)

    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setenv("VARIFLIGHT_API_URL", "https://example.test/mcp")
    monkeypatch.setattr(variflight_adapter.requests, "post", fake_post)

    results = variflight_adapter.search_variflight_flights("PVG", "HGH", "2026-08-16")

    assert len(results) == 1
    assert results[0] == {
        "flight_no": "MU6549",
        "origin": "SHA",
        "destination": "HGH",
        "depart_time": "23:30",
        "arrive_time": "00:40",
        "depart_date": "2026-08-16",
        "arrive_date": "2026-08-17",
        "duration": "1h10m",
        "price": "230",
        "summary": "上海浦东(PVG) -> 杭州萧山(HGH)",
        "provider": "VariFlight",
        "airline": "MU",
        "cabin": "经济舱",
        "airport": "上海浦东(PVG) T1 -> 杭州萧山(HGH) T3",
        "seat_count": 10,
        "is_demo": False,
    }
    assert calls == [
        {
            "url": "https://example.test/mcp",
            "headers": {
                "X-VARIFLIGHT-KEY": "test-key",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "json": {
                "endpoint": "getFlightPriceByCities",
                "params": {
                    "dep_city": "SHA",
                    "arr_city": "HGH",
                    "dep_date": "2026-08-16",
                    "price_mode": "lowest",
                },
            },
            "timeout": 20,
        }
    ]


def test_variflight_maps_airport_codes_to_city_codes(monkeypatch):
    captured = {}

    def fake_post(_url, *, json, **_kwargs):
        captured["json"] = json
        return _FakeResponse(200, {"code": 200, "message": "Success", "data": []})

    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(variflight_adapter.requests, "post", fake_post)

    assert variflight_adapter.search_variflight_flights("PEK", "PVG", "2026-08-16") == []
    assert captured["json"]["params"] == {
        "dep_city": "BJS",
        "arr_city": "SHA",
        "dep_date": "2026-08-16",
        "price_mode": "lowest",
    }


def test_variflight_provider_errors_are_not_treated_as_empty_results(monkeypatch):
    def fake_post(*_args, **_kwargs):
        return _FakeResponse(200, {"code": 401, "message": "invalid key", "data": []})

    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(variflight_adapter.requests, "post", fake_post)

    with pytest.raises(variflight_adapter.VariFlightError, match="invalid key"):
        variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")


def test_variflight_is_disabled_without_key(monkeypatch):
    monkeypatch.delenv("VARIFLIGHT_API_KEY", raising=False)

    assert variflight_adapter.is_variflight_configured() is False
    with pytest.raises(variflight_adapter.VariFlightError, match="not configured"):
        variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")


def test_variflight_uses_query_date_and_normalizes_clock_only_overnight_rows(monkeypatch):
    payload = {
        "code": 200,
        "data": [
            {
                "flightno": "MU0001",
                "flightdepcode": "PVG",
                "flightarrcode": "HGH",
                "deptime": "23:30",
                "arrtime": "00:40",
                "cabins": [{"classname": "经济舱", "price": 230, "seatnum": 3}],
            }
        ],
    }

    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(200, payload),
    )

    result = variflight_adapter.search_variflight_flights("PVG", "HGH", "2026-08-16")[0]

    assert result["depart_date"] == "2026-08-16"
    assert result["arrive_date"] == "2026-08-17"
    assert result["duration"] == "1h10m"


def test_variflight_drops_zero_inventory_fares(monkeypatch):
    payload = {
        "code": 200,
        "data": [
            {
                "flightno": "MU0002",
                "deptime": "10:00",
                "arrtime": "11:00",
                "cabins": [
                    {"classname": "经济舱", "price": 100, "seatnum": 0},
                    {"classname": "经济舱", "price": 230, "seatnum": 2},
                ],
            },
            {
                "flightno": "MU0003",
                "deptime": "12:00",
                "arrtime": "13:00",
                "cabins": [{"classname": "经济舱", "price": 180, "seatnum": 0}],
            },
        ],
    }
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(200, payload),
    )

    results = variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")

    assert [item["flight_no"] for item in results] == ["MU0002"]
    assert results[0]["price"] == "230"
    assert results[0]["seat_count"] == 2


def test_variflight_retries_rate_limit_once(monkeypatch):
    responses = iter(
        [
            _FakeResponse(429, {"code": 429, "message": "rate limited"}),
            _FakeResponse(200, {"code": 200, "data": []}),
        ]
    )
    calls = []
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setenv("VARIFLIGHT_MAX_RETRIES", "1")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: calls.append(True) or next(responses),
    )
    monkeypatch.setattr(variflight_adapter.time, "sleep", lambda *_args: None)

    assert variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16") == []
    assert len(calls) == 2


def test_variflight_http_401_is_an_authorization_failure(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(401, {"message": "unauthorized"}),
    )

    with pytest.raises(variflight_adapter.VariFlightError) as exc_info:
        variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")

    assert exc_info.value.failure_kind == "unauthorized"
    assert exc_info.value.http_status == 401


def test_variflight_nonempty_malformed_rows_are_schema_errors(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {"code": 200, "data": [{"flightno": "MU9999", "cabins": "changed"}]},
        ),
    )

    with pytest.raises(variflight_adapter.VariFlightError) as exc_info:
        variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")

    assert exc_info.value.failure_kind == "schema_changed"


def test_variflight_mixed_rows_preserve_valid_results_and_diagnostics(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {
                "code": 200,
                "data": [
                    {
                        "flightno": "MU0004",
                        "deptime": "10:00",
                        "arrtime": "11:00",
                        "cabins": [{"price": 230, "seatnum": 2}],
                    },
                    {"flightno": "MU9999", "cabins": "changed"},
                ],
            },
        ),
    )

    results = variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")

    assert [item["flight_no"] for item in results] == ["MU0004"]
    assert results.errors == ["provider_rows_schema_changed:1"]


def test_variflight_rejects_unknown_three_letter_codes(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")

    with pytest.raises(variflight_adapter.VariFlightError) as exc_info:
        variflight_adapter.search_variflight_flights("ZZZ", "HGH", "2026-08-16")

    assert exc_info.value.failure_kind == "invalid_input"


def test_variflight_accepts_operator_city_code_aliases(monkeypatch):
    captured = {}
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setenv(
        "VARIFLIGHT_CITY_CODE_ALIASES_JSON",
        '{"LJG":"LJG","丽江":"LJG"}',
    )
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda _url, *, json, **_kwargs: (
            captured.__setitem__("json", json),
            _FakeResponse(200, {"code": 200, "data": []}),
        )[1],
    )

    variflight_adapter.search_variflight_flights("丽江", "HGH", "2026-08-16")

    assert captured["json"]["params"]["dep_city"] == "LJG"


def test_variflight_negative_inventory_is_not_available(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {
                "code": 200,
                "data": [
                    {
                        "flightno": "MU0005",
                        "deptime": "10:00",
                        "arrtime": "11:00",
                        "cabins": [{"price": 230, "seatnum": -1}],
                    }
                ],
            },
        ),
    )

    assert variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16") == []


def test_variflight_negative_price_cannot_be_normalized_as_positive(monkeypatch):
    monkeypatch.setenv("VARIFLIGHT_API_KEY", "test-key")
    monkeypatch.setattr(
        variflight_adapter.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {
                "code": 200,
                "data": [
                    {
                        "flightno": "MU0006",
                        "deptime": "10:00",
                        "arrtime": "11:00",
                        "cabins": [{"price": -230, "seatnum": 2}],
                    }
                ],
            },
        ),
    )

    with pytest.raises(variflight_adapter.VariFlightError) as exc_info:
        variflight_adapter.search_variflight_flights("SHA", "HGH", "2026-08-16")

    assert exc_info.value.failure_kind == "schema_changed"
