import json
from urllib.parse import parse_qs, urlsplit

from adapters import rail_12306_adapter as rail


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class _FakeOpener:
    def __init__(self, calls):
        self.calls = calls

    def open(self, request, timeout=None):
        self.calls.append((urlsplit(request.full_url).path, timeout))
        return _FakeResponse(
            {
                "httpstatus": "200",
                "data": {"result": ["row"], "map": {}},
            }
        )


def test_ticket_payload_uses_short_cache_and_normal_query_only(monkeypatch):
    calls = []
    rail._ticket_cache.clear()
    monkeypatch.setenv("RAIL_MAX_RETRIES", "0")
    monkeypatch.setenv("RAIL_TICKET_CACHE_TTL_SECONDS", "60")
    monkeypatch.setattr(rail, "_build_opener", lambda timeout_seconds=None: _FakeOpener(calls))

    first = rail._load_ticket_payload("2026-09-20", "SHH", "HZH")
    second = rail._load_ticket_payload("2026-09-20", "SHH", "HZH")

    assert first == second
    assert len(calls) == 1
    assert calls[0][0].endswith("/leftTicket/query")


def test_ticket_payload_can_use_one_fallback_when_configured(monkeypatch):
    calls = []
    rail._ticket_cache.clear()
    monkeypatch.setenv("RAIL_MAX_RETRIES", "1")
    monkeypatch.setenv("RAIL_TICKET_CACHE_TTL_SECONDS", "0")

    class FailingFirstOpener(_FakeOpener):
        def open(self, request, timeout=None):
            calls.append((urlsplit(request.full_url).path, timeout))
            if request.full_url.endswith("/query?leftTicketDTO.train_date=2026-09-20&leftTicketDTO.from_station=SHH&leftTicketDTO.to_station=HZH&purpose_codes=ADULT"):
                raise TimeoutError("slow query")
            return _FakeResponse(
                {"httpstatus": "200", "data": {"result": ["row"], "map": {}}}
            )

    monkeypatch.setattr(rail, "_build_opener", lambda timeout_seconds=None: FailingFirstOpener(calls))
    result = rail._load_ticket_payload("2026-09-20", "SHH", "HZH")

    assert result["httpstatus"] == "200"
    assert len(calls) == 2
    assert calls[1][0].endswith("/leftTicket/queryA")
