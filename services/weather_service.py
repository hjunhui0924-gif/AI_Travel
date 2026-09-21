from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.schemas import CareReminder, DailyWeather, PlanItem
from utils.weather_utils import format_weather_text, get_amap_weather, has_amap_key


CN_TZ = ZoneInfo("Asia/Shanghai")


def get_weather_summary(location: str, forecast: bool = False) -> str:
    if not location or not has_amap_key():
        return ""
    return format_weather_text(location, forecast=forecast)


def _temperature(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _rain_probability(cast: dict) -> float | None:
    for key in ("rain_probability", "rainprobability", "pop", "precipitation_probability"):
        value = cast.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(str(value).replace("%", ""))
        except ValueError:
            continue
        return parsed / 100 if parsed > 1 else parsed
    return None


def get_daily_weather(location: str) -> list[DailyWeather]:
    """Normalize provider forecast casts without inventing missing fields."""

    if not location or not has_amap_key():
        return []
    payload = get_amap_weather(location, forecast=True)
    retrieved_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    result: list[DailyWeather] = []
    for cast in payload.get("casts") or []:
        if not isinstance(cast, dict):
            continue
        day = str(cast.get("date") or "").strip()
        if not day:
            continue
        result.append(
            DailyWeather(
                date=day,
                day_weather=str(cast.get("dayweather") or ""),
                night_weather=str(cast.get("nightweather") or ""),
                day_temp_c=_temperature(cast.get("daytemp")),
                night_temp_c=_temperature(cast.get("nighttemp")),
                rain_probability=_rain_probability(cast),
                wind_level=str(cast.get("daypower") or cast.get("nightpower") or ""),
                humidity=str(cast.get("humidity") or ""),
                source_id=f"weather_{day}",
                retrieved_at=retrieved_at,
            )
        )
    return result


def build_care_reminders(
    daily_weather: list[DailyWeather],
    items: list[PlanItem],
) -> list[CareReminder]:
    """Turn weather facts into modest, date-specific action reminders."""

    outdoor_by_date: dict[str, bool] = {}
    for item in items:
        if item.item_type in {"attraction", "activity", "route"}:
            outdoor_by_date[item.date] = True
    reminders: list[CareReminder] = []
    for weather in daily_weather:
        date_text = weather.date
        sources = [weather.source_id] if weather.source_id else []
        weather_text = f"{weather.day_weather} {weather.night_weather}"
        has_outdoor = outdoor_by_date.get(date_text, False)
        if weather.rain_probability is not None and weather.rain_probability >= 0.5:
            reminders.append(CareReminder(date_text, "降雨概率较高，准备雨具；户外安排可备一个室内替代地点。", "rain", sources))
        elif any(token in weather_text for token in ("雨", "雷")):
            reminders.append(CareReminder(date_text, "天气预报含降雨或雷雨信息，准备雨具并关注临近预报。", "rain_signal", sources))
        if weather.day_temp_c is not None and weather.day_temp_c >= 33 and has_outdoor:
            reminders.append(CareReminder(date_text, "白天高温，建议防晒、帽子和饮水，尽量避开正午长时间户外活动。", "high_temperature", sources))
        if weather.night_temp_c is not None and weather.night_temp_c <= 5:
            reminders.append(CareReminder(date_text, "早晚偏冷，准备外套并留意温差。", "low_temperature", sources))
        wind = _temperature(weather.wind_level)
        if (wind is not None and wind >= 6) or any(token in weather_text for token in ("大风", "雷暴", "台风")):
            reminders.append(CareReminder(date_text, "风力或强对流风险较高，减少高处、水上和长时间户外活动。", "strong_wind", sources))
        humidity = _temperature(weather.humidity)
        if humidity is not None and humidity >= 80:
            reminders.append(CareReminder(date_text, "湿度较高，选择透气衣物、及时补水并安排适当休息。", "high_humidity", sources))
    return reminders
