from __future__ import annotations

from agents.schemas import PoiGroup, PoiRecommendation, RoutePlan, TimelineItem, TransportOption, TravelQuery


def _poi_for_place(place: str, poi_recommendations: list[PoiRecommendation]) -> list[PoiRecommendation]:
    return [item for item in poi_recommendations if place in item.summary or place in item.name][:2]


def _group_for_place(place: str, poi_groups: list[PoiGroup]) -> list[PoiRecommendation]:
    for group in poi_groups:
        if place in group.anchor or group.anchor in place:
            return group.items[:2]
    return []


def build_timeline(
    query: TravelQuery,
    transport_options: list[TransportOption],
    route_plans: list[RoutePlan] | None = None,
    poi_recommendations: list[PoiRecommendation] | None = None,
    poi_groups: list[PoiGroup] | None = None,
) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    route_plans = route_plans or []
    poi_recommendations = poi_recommendations or []
    poi_groups = poi_groups or []

    if transport_options:
        best = transport_options[0]
        items.append(
            TimelineItem(
                time_label=best.depart_time or "待定",
                title=f"从 {query.origin or '出发地'} 前往 {query.destination or '目的地'}",
                detail=f"建议优先关注 {best.mode} 方案 {best.title}，预计历时 {best.duration or '待补充'}。",
            )
        )

    for index, route in enumerate(route_plans, start=1):
        items.append(
            TimelineItem(
                time_label=f"市内段 {index}",
                title=f"从 {route.origin} 前往 {route.destination}",
                detail=(
                    f"建议使用 {route.mode}，约 {route.duration or '时长待补充'}，距离 {route.distance or '待补充'}。"
                    f"出发地：{route.origin_address or route.origin}。"
                    f"目的地：{route.destination_address or route.destination}。"
                    f"{route.summary or ''}"
                ),
            )
        )

    for index, place in enumerate(query.named_places, start=1):
        nearby = _group_for_place(place, poi_groups) or _poi_for_place(place, poi_recommendations)
        detail = "可结合酒店、会场、景点和餐饮的实际顺序继续细化停留时长与出发时间。"
        if nearby:
            nearby_label = "；".join([item.name for item in nearby])
            detail = f"{detail} 附近可优先考虑：{nearby_label}。"
        items.append(
            TimelineItem(
                time_label=f"到点后 {index}",
                title=f"处理地点：{place}",
                detail=detail,
            )
        )

    if query.destination:
        items.append(
            TimelineItem(
                time_label="晚间",
                title="预留弹性探索时间",
                detail="可安排附近餐馆、夜游点或轻量散步路线，形成出行与游玩结合的闭环。",
            )
        )
    return items
