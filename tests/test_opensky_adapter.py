from __future__ import annotations

from adapters import opensky_adapter


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_opensky_oauth_and_state_vectors_are_normalized(monkeypatch):
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "client")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "secret")
    opensky_adapter._token_value = ""
    opensky_adapter._token_expires_at = 0
    calls = []

    def fake_post(url, **kwargs):
        calls.append(("post", url, kwargs))
        return FakeResponse({"access_token": "token", "expires_in": 1800})

    def fake_get(url, **kwargs):
        calls.append(("get", url, kwargs))
        return FakeResponse(
            {
                "time": 1780000000,
                "states": [
                    [
                        "abc123", "  CA123  ", "China", 1780000000, 1780000000,
                        116.4, 39.9, 9000, False, 220, 85, 0, None, 9100, "", "", 0,
                    ]
                ],
            }
        )

    monkeypatch.setattr(opensky_adapter.requests, "post", fake_post)
    monkeypatch.setattr(opensky_adapter.requests, "get", fake_get)

    result = opensky_adapter.search_opensky_states()

    assert len(result) == 1
    assert result[0].callsign == "CA123"
    assert result[0].origin_country == "China"
    assert result[0].latitude == 39.9
    assert result[0].on_ground is False
    assert calls[0][0] == "post"
    assert calls[1][0] == "get"
    assert calls[1][2]["headers"]["Authorization"] == "Bearer token"


def test_opensky_can_use_downloaded_credentials_file(monkeypatch, tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text('{"clientId":"file-client","clientSecret":"file-secret"}', encoding="utf-8")
    monkeypatch.setenv("OPENSKY_CREDENTIALS_FILE", str(credentials))

    assert opensky_adapter._credentials() == ("file-client", "file-secret")
