from __future__ import annotations

from utils.weather_utils import format_weather_text, has_amap_key


def get_weather_summary(location: str, forecast: bool = False) -> str:
    if not location or not has_amap_key():
        return ""
    return format_weather_text(location, forecast=forecast)
