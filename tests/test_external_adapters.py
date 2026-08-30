import json
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from adapters import amap_adapter
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


class _FakeImageHeaders:
    def get_content_type(self):
        return "image/png"


class _FakeImageResponse:
    headers = _FakeImageHeaders()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b"fake-png"


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


def test_amap_route_normalizes_step_polylines(monkeypatch):
    geocoded = {
        "西湖": {
            "location": "120.121358,30.222692",
            "formatted_address": "杭州市西湖",
            "city": "杭州",
        },
        "灵隐寺": {
            "location": "120.101406,30.240826",
            "formatted_address": "杭州市灵隐寺",
            "city": "杭州",
        },
    }
    monkeypatch.setattr(amap_adapter, "has_amap_key", lambda: True)
    monkeypatch.setattr(amap_adapter, "safe_geocode", lambda name: geocoded[name])
    monkeypatch.setattr(
        amap_adapter,
        "_amap_get",
        lambda path, params: {
            "route": {
                "paths": [
                    {
                        "distance": "2450",
                        "duration": "900",
                        "steps": [
                            {"polyline": "120.121358,30.222692;120.115,30.230"},
                            {"polyline": "120.115,30.230;120.101406,30.240826"},
                        ],
                    }
                ]
            }
        },
    )

    result = amap_adapter.plan_route("西湖", "灵隐寺", strategy="walking")

    assert result is not None
    assert result["origin_location"] == "120.121358,30.222692"
    assert result["destination_location"] == "120.101406,30.240826"
    assert result["polyline"] == [
        [120.121358, 30.222692],
        [120.115, 30.23],
        [120.101406, 30.240826],
    ]


def test_route_map_svg_fallback_preserves_route_geometry():
    payload = amap_adapter.render_route_map_svg(
        [
            {
                "polyline": [[120.0, 30.0], [120.05, 30.02], [120.1, 30.1]],
            }
        ]
    )

    rendered = payload.decode("utf-8")

    assert payload.startswith(b"<svg")
    assert "polyline" in rendered
    assert "路线示意图" in rendered


def test_static_route_map_uses_distinct_styles_for_multiple_paths(monkeypatch):
    monkeypatch.setenv("AMAP_WEB_API_KEY", "a" * 32)
    monkeypatch.delenv("AMAP_STATIC_MAP_KEY", raising=False)
    captured: dict[str, list[str]] = {}

    def fake_urlopen(request, **kwargs):
        captured.update(parse_qs(urlsplit(request.full_url).query))
        return _FakeImageResponse()

    monkeypatch.setattr(amap_adapter.urllib.request, "urlopen", fake_urlopen)

    result = amap_adapter.fetch_static_route_map(
        [
            {"polyline": [[120.0, 30.0], [120.05, 30.02]]},
            {"polyline": [[120.05, 30.02], [120.1, 30.04]]},
        ]
    )

    assert result == (b"fake-png", "image/png")
    path_entries = captured["paths"][0].split("|")
    assert all(":" in entry for entry in path_entries)
    path_styles = [entry.split(":", 1)[0] for entry in path_entries]
    assert len(path_styles) == 2
    assert path_styles[0] != path_styles[1]
