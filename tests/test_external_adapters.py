import json
import urllib.request

import pytest

from utils import weather_utils


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def test_amap_error_keeps_provider_code_without_exposing_key(monkeypatch):
    monkeypatch.setenv("AMAP_WEB_API_KEY", "a" * 32)
    monkeypatch.setenv("AMAP_MIN_REQUEST_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("AMAP_MAX_RETRIES", "0")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeResponse(
            {
                "status": "0",
                "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                "infocode": "10021",
            }
        ),
    )

    with pytest.raises(weather_utils.AmapApiError) as exc_info:
        weather_utils._amap_get("/v3/geocode/geo", {"address": "杭州"})

    error = exc_info.value
    assert error.infocode == "10021"
    assert "CUQPS_HAS_EXCEEDED_THE_LIMIT" in str(error)
    assert "a" * 32 not in str(error)
