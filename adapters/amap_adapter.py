from __future__ import annotations

import math
import os
import urllib.parse
import urllib.request

from utils.weather_utils import _amap_get, geocode_location, has_amap_key


def safe_geocode(location: str) -> dict | None:
    if not location or not has_amap_key():
        return None
    return geocode_location(location)


def search_pois(location: str, keywords: str, page_size: int = 5) -> list[dict]:
    geo = safe_geocode(location)
    if not geo:
        return []

    city = geo.get("city") or geo.get("province") or location
    payload = _amap_get(
        "/v5/place/text",
        {
            "keywords": keywords,
            "region": city,
            "page_size": page_size,
            "show_fields": "business,photos,indoor,navi,discount_tag,rating,cost",
        },
    )

    pois = payload.get("pois") or []
    results = []
    for item in pois[:page_size]:
        business = item.get("business", {}) if isinstance(item.get("business"), dict) else {}
        results.append(
            {
                "provider_id": item.get("id", ""),
                "name": item.get("name", ""),
                "category": item.get("type", ""),
                "address": item.get("address", ""),
                "area": item.get("cityname") or item.get("adname") or city,
                "province": item.get("pname", ""),
                "city": item.get("cityname") or city,
                "district": item.get("adname", ""),
                "location": item.get("location", ""),
                "distance": item.get("distance", ""),
                "rating": business.get("rating", ""),
                "cost": business.get("cost", ""),
            }
        )
    return results


def search_place_candidates(city: str, place: str, page_size: int = 3) -> list[dict]:
    """Return map-backed candidates for an explicit user place.

    Unlike ``resolve_place_in_city`` this intentionally keeps multiple
    candidates so same-name landmarks can be confirmed instead of selecting
    the provider's first result silently.
    """

    if not city or not place or not has_amap_key():
        return []
    payload = _amap_get(
        "/v5/place/text",
        {
            "keywords": place,
            "region": city,
            "page_size": max(1, min(3, int(page_size))),
            "show_fields": "business,navi",
        },
    )
    candidates = []
    for item in (payload.get("pois") or [])[: max(1, min(3, int(page_size)))]:
        if not isinstance(item, dict):
            continue
        business = item.get("business", {}) if isinstance(item.get("business"), dict) else {}
        candidates.append(
            {
                "provider_id": str(item.get("id") or ""),
                "name": str(item.get("name") or place),
                "category": str(item.get("type") or ""),
                "province": str(item.get("pname") or ""),
                "city": str(item.get("cityname") or city),
                "district": str(item.get("adname") or ""),
                "address": str(item.get("address") or ""),
                "location": str(item.get("location") or ""),
                "distance": str(item.get("distance") or ""),
                "rating": str(business.get("rating") or ""),
            }
        )
    return candidates


def resolve_place_in_city(city: str, place: str) -> dict | None:
    if not city or not place or not has_amap_key():
        return None

    payload = _amap_get(
        "/v5/place/text",
        {
            "keywords": place,
            "region": city,
            "page_size": 1,
            "show_fields": "business,navi",
        },
    )

    pois = payload.get("pois") or []
    if not pois:
        return None

    item = pois[0]
    return {
        "provider_id": item.get("id", ""),
        "name": item.get("name", place),
        "category": item.get("type", ""),
        "province": item.get("pname", ""),
        "address": item.get("address", ""),
        "city": city,
        "district": item.get("adname", ""),
        "location": item.get("location", ""),
        "formatted_address": f"{city}{item.get('address', '')}".strip(),
    }


def search_pois_around_location(
    location: str,
    keywords: str = "",
    page_size: int = 5,
    radius: int = 2000,
    types: str = "",
) -> list[dict]:
    if not location or not has_amap_key():
        return []

    params = {
        "location": location,
        "radius": radius,
        "page_size": page_size,
        "show_fields": "business,photos,indoor,navi,discount_tag,rating,cost",
    }
    if keywords:
        params["keywords"] = keywords
    if types:
        params["types"] = types

    payload = _amap_get("/v3/place/around", params)

    pois = payload.get("pois") or []
    results = []
    for item in pois[:page_size]:
        business = item.get("business", {}) if isinstance(item.get("business"), dict) else {}
        results.append(
            {
                "name": item.get("name", ""),
                "category": item.get("type", ""),
                "address": item.get("address", ""),
                "area": "",
                "distance": item.get("distance", ""),
                "rating": business.get("rating", ""),
                "cost": business.get("cost", ""),
                "location": item.get("location", ""),
            }
        )
    return results


def _parse_location_point(value: object) -> list[float] | None:
    raw = str(value or "").strip()
    parts = raw.split(",")
    if len(parts) != 2:
        return None
    try:
        longitude, latitude = (float(part.strip()) for part in parts)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        return None
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        return None
    return [round(longitude, 6), round(latitude, 6)]


def _append_polyline_points(value: object, points: list[list[float]]) -> None:
    if not isinstance(value, str):
        return
    for raw_point in value.split(";"):
        point = _parse_location_point(raw_point)
        if point and (not points or point != points[-1]):
            points.append(point)


def _collect_polyline_points(value: object, points: list[list[float]] | None = None) -> list[list[float]]:
    """Flatten walking/driving/transit polyline fields from an AMap route."""

    result = points if points is not None else []
    if isinstance(value, dict):
        _append_polyline_points(value.get("polyline"), result)
        for key, child in value.items():
            if key != "polyline":
                _collect_polyline_points(child, result)
    elif isinstance(value, list):
        for child in value:
            _collect_polyline_points(child, result)
    return result


def _downsample_points(points: list[list[float]], limit: int = 900) -> list[list[float]]:
    if len(points) <= limit:
        return points
    # Preserve both endpoints and distribute the remaining samples evenly.
    last_index = len(points) - 1
    indexes = [round(index * last_index / (limit - 1)) for index in range(limit)]
    return [points[index] for index in indexes]


def plan_route(origin: str, destination: str, strategy: str = "walking") -> dict | None:
    if not origin or not destination or not has_amap_key():
        return None

    # Route service passes already-resolved coordinates. Accepting them here
    # avoids a second unscoped text search (which can resolve a landmark in a
    # different city) while keeping the public name-based call compatible.
    origin_point = _parse_location_point(origin)
    destination_point = _parse_location_point(destination)
    origin_geo = (
        {"location": origin, "formatted_address": origin}
        if origin_point
        else safe_geocode(origin)
    )
    destination_geo = (
        {"location": destination, "formatted_address": destination}
        if destination_point
        else safe_geocode(destination)
    )
    if not origin_geo or not destination_geo:
        return None

    origin_location = origin_geo.get("location")
    destination_location = destination_geo.get("location")
    if not origin_location or not destination_location:
        return None

    path = {
        "walking": "/v3/direction/walking",
        "driving": "/v3/direction/driving",
        "transit": "/v3/direction/transit/integrated",
    }.get(strategy, "/v3/direction/walking")

    params = {
        "origin": origin_location,
        "destination": destination_location,
    }
    if strategy == "transit":
        transit_city = destination_geo.get("adcode") or destination_geo.get("city")
        if transit_city:
            params["city"] = transit_city

    payload = _amap_get(path, params)

    route = payload.get("route", {})
    paths = route.get("paths") or route.get("transits") or []
    if not paths:
        return None

    best = paths[0]
    polyline = _downsample_points(_collect_polyline_points(best))
    distance = best.get("distance", "")
    duration = best.get("duration", "")
    distance_meters = int(distance) if str(distance).isdigit() else None
    duration_minutes = max(1, round(int(duration) / 60)) if str(duration).isdigit() else None
    if duration and str(duration).isdigit():
        duration = f"{duration_minutes} 分钟"

    summary = ""
    estimated_cost = None
    cost_currency = ""
    cost_scope = ""
    if strategy == "transit":
        cost = best.get("cost", "")
        walking_distance = best.get("walking_distance", "")
        parts = []
        if cost:
            parts.append(f"票价 {cost}")
            try:
                estimated_cost = float(str(cost).replace(",", ""))
                cost_currency = "CNY"
                cost_scope = "route"
            except ValueError:
                estimated_cost = None
        if walking_distance:
            parts.append(f"步行 {walking_distance} 米")
        summary = " | ".join(parts)
    else:
        steps = best.get("steps") or []
        if steps:
            summary = steps[0].get("instruction", "")

    return {
        "mode": strategy,
        "origin": origin_geo.get("formatted_address", origin),
        "destination": destination_geo.get("formatted_address", destination),
        "distance": f"{distance} 米" if distance and str(distance).isdigit() else str(distance),
        "duration": duration,
        "summary": summary,
        "duration_minutes": duration_minutes,
        "distance_meters": distance_meters,
        "estimated_cost": estimated_cost,
        "cost_currency": cost_currency,
        "cost_scope": cost_scope,
        "origin_location": origin_location,
        "destination_location": destination_location,
        "polyline": polyline,
    }


def fetch_static_route_map(
    route_plans: list[dict],
    *,
    size: str = "750*420",
) -> tuple[bytes, str] | None:
    """Fetch an AMap static map image without exposing the Web Service key."""

    if not has_amap_key() or not route_plans:
        return None

    paths: list[str] = []
    all_points: list[list[float]] = []
    marker_points: list[list[float]] = []
    path_styles = (
        ("7", "0x1E7CF0", "0.9"),
        ("11", "0xC45F3C", "1"),
        ("8", "0x4D7896", "0.9"),
        ("13", "0x9A6A0A", "1"),
    )
    # AMap documents a maximum of four path overlays. Keep the request within
    # that limit, and give each overlay a distinct style. Repeating the exact
    # same style for multiple path groups currently makes Static Map return
    # UNKNOWN_ERROR (20003), even though each individual path is valid.
    for route_index, route in enumerate(route_plans[:4]):
        raw_points = route.get("polyline") if isinstance(route, dict) else []
        points = []
        if isinstance(raw_points, list):
            for raw_point in raw_points:
                if isinstance(raw_point, (list, tuple)) and len(raw_point) == 2:
                    point = _parse_location_point(f"{raw_point[0]},{raw_point[1]}")
                    if point and (not points or point != points[-1]):
                        points.append(point)
        points = _downsample_points(points, limit=180)
        if len(points) < 2:
            continue
        all_points.extend(points)
        encoded_points = ";".join(f"{point[0]},{point[1]}" for point in points)
        weight, color, transparency = path_styles[route_index % len(path_styles)]
        # AMap's paths grammar requires a colon between the style tuple and
        # the first coordinate: ``weight,color,transparency,fillcolor,fillTransparency:points``.
        paths.append(f"{weight},{color},{transparency},,:{encoded_points}")
        marker_points.extend([points[0], points[-1]])

    if not paths:
        return None

    all_longitudes = [point[0] for point in all_points]
    all_latitudes = [point[1] for point in all_points]
    center = f"{(min(all_longitudes) + max(all_longitudes)) / 2:.6f},{(min(all_latitudes) + max(all_latitudes)) / 2:.6f}"
    span = max(max(all_longitudes) - min(all_longitudes), max(all_latitudes) - min(all_latitudes))
    zoom = 15 if span < 0.04 else 13 if span < 0.12 else 11 if span < 0.4 else 9 if span < 1.5 else 6

    markers: list[str] = []
    for index, point in enumerate(marker_points):
        label = chr(65 + min(index, 25))
        markers.append(f"mid,,{label}:{point[0]},{point[1]}")
    params = {
        "location": center,
        "zoom": str(zoom),
        "size": size,
        "scale": "2",
        "paths": "|".join(paths),
        "markers": "|".join(markers),
        # Static Map may be enabled on a separate AMap Web Service key. Fall
        # back to the existing key so one-key local setups keep working.
        "key": (
            os.getenv("AMAP_STATIC_MAP_KEY", "").strip()
            or os.getenv("AMAP_WEB_API_KEY", "").strip()
        ),
    }
    request = urllib.request.Request(
        "https://restapi.amap.com/v3/staticmap?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "AI-Travel-Agent/1.0"},
    )
    timeout = float(os.getenv("AMAP_HTTP_TIMEOUT_SECONDS", "20"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        payload = response.read()
    if not content_type.startswith("image/"):
        return None
    return payload, content_type


def render_route_map_svg(route_plans: list[dict], *, width: int = 700, height: int = 420) -> bytes:
    """Render a compact route map fallback from verified AMap geometry.

    This is deliberately a schematic, not a replacement for AMap tiles. It
    keeps the route visible when a Web Service key lacks Static Map permission
    and avoids pretending that a provider map was successfully loaded.
    """

    routes: list[list[list[float]]] = []
    for route in route_plans:
        raw_points = route.get("polyline") if isinstance(route, dict) else []
        points: list[list[float]] = []
        if isinstance(raw_points, list):
            for raw_point in raw_points:
                if isinstance(raw_point, (list, tuple)) and len(raw_point) == 2:
                    point = _parse_location_point(f"{raw_point[0]},{raw_point[1]}")
                    if point and (not points or point != points[-1]):
                        points.append(point)
        points = _downsample_points(points, limit=320)
        if len(points) >= 2:
            routes.append(points)

    if not routes:
        return b""

    all_points = [point for route in routes for point in route]
    min_longitude = min(point[0] for point in all_points)
    max_longitude = max(point[0] for point in all_points)
    min_latitude = min(point[1] for point in all_points)
    max_latitude = max(point[1] for point in all_points)
    longitude_span = max(max_longitude - min_longitude, 0.000001)
    latitude_span = max(max_latitude - min_latitude, 0.000001)
    pad_x = 24
    pad_y = 24
    inner_width = width - pad_x * 2
    inner_height = height - pad_y * 2

    def project(point: list[float]) -> tuple[float, float]:
        x = pad_x + ((point[0] - min_longitude) / longitude_span) * inner_width
        y = height - pad_y - ((point[1] - min_latitude) / latitude_span) * inner_height
        return round(x, 2), round(y, 2)

    line_markup: list[str] = []
    marker_markup: list[str] = []
    colors = ["#0e5f54", "#c45f3c", "#4d7896", "#9a6a0a", "#6d668f", "#4f7b59"]
    for index, route in enumerate(routes):
        projected = [project(point) for point in route]
        points_attribute = " ".join(f"{x},{y}" for x, y in projected)
        color = colors[index % len(colors)]
        line_markup.append(
            f'<polyline points="{points_attribute}" fill="none" stroke="{color}" '
            f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )
        start_x, start_y = projected[0]
        end_x, end_y = projected[-1]
        marker_markup.extend(
            [
                f'<circle cx="{start_x}" cy="{start_y}" r="9" fill="#f3b568" stroke="#fffaf0" stroke-width="3"/>',
                f'<circle cx="{end_x}" cy="{end_y}" r="9" fill="#c45f3c" stroke="#fffaf0" stroke-width="3"/>',
            ]
        )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="路线示意图">
<defs>
  <pattern id="route-grid" width="42" height="42" patternUnits="userSpaceOnUse">
    <path d="M 42 0 L 0 0 0 42" fill="none" stroke="#2f5c5b" stroke-opacity=".13" stroke-width="1"/>
  </pattern>
  <linearGradient id="route-wash" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#e6f0e5"/>
    <stop offset="1" stop-color="#f2eadc"/>
  </linearGradient>
</defs>
<rect width="100%" height="100%" fill="url(#route-wash)"/>
<rect width="100%" height="100%" fill="url(#route-grid)"/>
<path d="M-30 {height - 90} C 120 {height - 160}, 210 {height - 36}, 340 {height - 112} S 550 {height - 205}, {width + 30} {height - 130}" fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="24"/>
<path d="M-30 {height - 90} C 120 {height - 160}, 210 {height - 36}, 340 {height - 112} S 550 {height - 205}, {width + 30} {height - 130}" fill="none" stroke="#5a847c" stroke-opacity=".18" stroke-width="2"/>
{''.join(line_markup)}
{''.join(marker_markup)}
<rect x="{width - 184}" y="{height - 45}" width="160" height="26" rx="13" fill="#fffaf0" fill-opacity=".88"/>
<text x="{width - 104}" y="{height - 28}" text-anchor="middle" fill="#094a41" font-family="sans-serif" font-size="12">路线示意图 · 基于高德路径</text>
</svg>'''
    return svg.encode("utf-8")
