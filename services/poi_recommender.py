from __future__ import annotations

from adapters.amap_adapter import resolve_place_in_city, search_pois, search_pois_around_location
from agents.schemas import PoiGroup, PoiRecommendation, TravelQuery


FOOD_TYPES = "050000|060000"
LEISURE_TYPES = "110000|120000|160000"


def _to_poi(item: dict, category: str, area: str, prefix: str = "") -> PoiRecommendation:
    summary_parts = []
    if item.get("rating"):
        summary_parts.append(f"评分 {item['rating']}")
    if item.get("cost"):
        summary_parts.append(f"人均 {item['cost']}")
    if item.get("address"):
        summary_parts.append(item["address"])
    if item.get("distance"):
        summary_parts.append(f"距离 {item['distance']} 米")

    summary = " | ".join(summary_parts) if summary_parts else "高德 POI 搜索结果"
    if prefix:
        summary = f"{prefix} | {summary}"

    return PoiRecommendation(
        name=item.get("name", ""),
        category=category,
        area=item.get("area", area),
        address=item.get("address", ""),
        distance=str(item.get("distance", "")),
        summary=summary,
    )


def _sort_by_distance(items: list[dict]) -> list[dict]:
    def key(item: dict) -> int:
        try:
            return int(item.get("distance") or 10**9)
        except Exception:
            return 10**9
    return sorted(items, key=key)


def build_poi_groups(query: TravelQuery) -> list[PoiGroup]:
    city = query.destination or query.city
    if not city:
        return []

    groups: list[PoiGroup] = []
    for place in query.named_places[:4]:
        resolved = resolve_place_in_city(city, place)
        if not resolved or not resolved.get("location"):
            continue

        food_items = search_pois_around_location(
            resolved["location"],
            page_size=4,
            radius=1800,
            types=FOOD_TYPES,
        )
        leisure_items = search_pois_around_location(
            resolved["location"],
            page_size=2,
            radius=2200,
            types=LEISURE_TYPES,
        )

        if not food_items:
            food_items = search_pois(resolved["name"], "附近餐厅 美食 咖啡馆", page_size=3)
        if not leisure_items:
            leisure_items = search_pois(resolved["name"], "附近景点 娱乐", page_size=2)

        items = []
        for item in _sort_by_distance(food_items)[:3]:
            items.append(_to_poi(item, "附近餐饮", city, prefix=f"围绕 {resolved['name']} 推荐"))
        for item in _sort_by_distance(leisure_items)[:2]:
            items.append(_to_poi(item, "附近去处", city, prefix=f"围绕 {resolved['name']} 推荐"))

        if items:
            groups.append(PoiGroup(anchor=resolved["name"], items=items))
    return groups


def recommend_pois(query: TravelQuery) -> tuple[list[PoiRecommendation], list[PoiGroup]]:
    city = query.destination or query.city
    if not city:
        return [], []

    poi_groups = build_poi_groups(query)
    recommendations: list[PoiRecommendation] = []
    for group in poi_groups:
        recommendations.extend(group.items)

    food_items = search_pois(city, "本地特色餐厅 美食", page_size=3)
    sight_items = search_pois(city, "热门景点 旅游", page_size=3)

    for item in food_items:
        recommendations.append(_to_poi(item, "美食", city))
    for item in sight_items:
        recommendations.append(_to_poi(item, "景点", city))

    deduped: list[PoiRecommendation] = []
    seen = set()
    for item in recommendations:
        key = (item.name, item.address)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    if deduped:
        return deduped[:12], poi_groups

    fallback = [
        PoiRecommendation(
            name=f"{city} 本地早茶/特色餐馆",
            category="美食",
            area=city,
            summary="当前未获取到高德真实 POI 结果，先保留占位推荐。",
        ),
        PoiRecommendation(
            name=f"{city} 热门地标与夜游路线",
            category="景点",
            area=city,
            summary="当前未获取到高德真实 POI 结果，先保留占位推荐。",
        ),
    ]
    return fallback, poi_groups
