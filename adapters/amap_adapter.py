from __future__ import annotations

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
                "name": item.get("name", ""),
                "category": item.get("type", ""),
                "address": item.get("address", ""),
                "area": city,
                "distance": item.get("distance", ""),
                "rating": business.get("rating", ""),
                "cost": business.get("cost", ""),
            }
        )
    return results


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
        "name": item.get("name", place),
        "address": item.get("address", ""),
        "city": city,
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


def plan_route(origin: str, destination: str, strategy: str = "walking") -> dict | None:
    if not origin or not destination or not has_amap_key():
        return None

    origin_geo = safe_geocode(origin)
    destination_geo = safe_geocode(destination)
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
        params["city"] = destination_geo.get("adcode") or destination_geo.get("city") or destination

    payload = _amap_get(path, params)

    route = payload.get("route", {})
    paths = route.get("paths") or route.get("transits") or []
    if not paths:
        return None

    best = paths[0]
    distance = best.get("distance", "")
    duration = best.get("duration", "")
    if duration and str(duration).isdigit():
        minutes = max(1, round(int(duration) / 60))
        duration = f"{minutes} 分钟"

    summary = ""
    if strategy == "transit":
        cost = best.get("cost", "")
        walking_distance = best.get("walking_distance", "")
        parts = []
        if cost:
            parts.append(f"票价 {cost}")
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
    }
