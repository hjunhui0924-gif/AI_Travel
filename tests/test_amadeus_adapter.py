from __future__ import annotations

import pytest

from adapters import amadeus_adapter
from adapters import flight_mcp_adapter


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


def _configure(monkeypatch):
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "client")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
    monkeypatch.setenv("AMADEUS_MAX_RETRIES", "1")
    amadeus_adapter._token_value = ""
    amadeus_adapter._token_expires_at = 0.0


def test_amadeus_normalizes_domestic_offer(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        amadeus_adapter.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"access_token": "token", "expires_in": 1800}),
    )
    monkeypatch.setattr(
        amadeus_adapter.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            200,
            {
                "data": [
                    {
                        "itineraries": [
                            {
                                "duration": "PT2H20M",
                                "segments": [
                                    {
                                        "carrierCode": "MU",
                                        "number": "5123",
                                        "departure": {"iataCode": "SHA", "at": "2026-10-01T09:15:00"},
                                        "arrival": {"iataCode": "HGH", "at": "2026-10-01T11:35:00"},
                                    }
                                ],
                            }
                        ],
                        "price": {"currency": "CNY", "total": "680.00"},
                        "numberOfBookableSeats": 7,
                        "travelerPricings": [{"fareDetailsBySegment": [{"cabin": "ECONOMY"}]}],
                    }
                ]
            },
        ),
    )

    result = amadeus_adapter.search_amadeus_flights("上海", "杭州", "2026-10-01")

    assert result.errors == []
    assert result[0]["flight_no"] == "MU5123"
    assert result[0]["depart_date"] == "2026-10-01"
    assert result[0]["price"] == "CNY 680.00"
    assert result[0]["seat_count"] == 7
    assert result[0]["is_demo"] is False


def test_amadeus_rejects_unauthorized_token(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        amadeus_adapter.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            401,
            {"error": "invalid_client", "error_description": "invalid credentials"},
        ),
    )

    with pytest.raises(amadeus_adapter.AmadeusError) as exc_info:
        amadeus_adapter.search_amadeus_flights("上海", "杭州", "2026-10-01")

    assert exc_info.value.failure_kind == "unauthorized"
    assert exc_info.value.http_status == 401


def test_flight_adapter_can_select_amadeus(monkeypatch):
    monkeypatch.setenv("FLIGHT_MCP_MODE", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "client")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        flight_mcp_adapter,
        "search_amadeus_flights",
        lambda origin, destination, date: [{"flight_no": "MU1", "origin": origin}],
    )

    assert flight_mcp_adapter.is_flight_mcp_enabled() is True
    assert flight_mcp_adapter.search_flights("上海", "杭州", "2026-10-01")[0]["flight_no"] == "MU1"
