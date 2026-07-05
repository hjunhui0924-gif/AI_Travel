from __future__ import annotations

import re
from datetime import datetime, timedelta

from adapters.ctrip_flight_adapter import probe_ctrip_flight_page
from agents.schemas import TravelPlanResponse, TravelQuery
from services.flight_service import get_flight_options
from services.poi_recommender import recommend_pois
from services.rail_service import get_rail_options
from services.route_service import get_route_plans
from services.trip_extractor import extract_attachment_notes, extract_named_places
from services.trip_planner import build_timeline
from services.weather_service import get_weather_summary


KNOWN_CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "苏州", "南京", "成都",
    "重庆", "武汉", "西安", "长沙", "厦门", "青岛", "湛江", "佛山",
]

RAIL_KEYWORDS = ["高铁", "火车", "动车", "12306", "车次", "车票", "余票"]
FLIGHT_KEYWORDS = ["飞机", "航班", "机票"]
NEARBY_KEYWORDS = ["附近", "周边", "景点", "好玩", "好吃", "餐厅", "咖啡馆", "推荐", "攻略"]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _has_cross_city_route(message: str, origin: str, destination: str) -> bool:
    if origin and destination and origin != destination:
        return True
    return bool(re.search(r"从.{1,8}到.{1,8}", message))


def _detect_intent(message: str, origin: str, destination: str) -> str:
    has_rail = _contains_any(message, RAIL_KEYWORDS)
    has_flight = _contains_any(message, FLIGHT_KEYWORDS)
    has_nearby = _contains_any(message, NEARBY_KEYWORDS)
    has_compare = _contains_any(message, ["对比", "比较", "哪个更好", "还是"])
    has_replan = "改" in message and _contains_any(message, ["行程", "路线", "方案", "晚点", "下雨"])
    has_cross_city = _has_cross_city_route(message, origin, destination)

    if has_replan:
        return "trip_replan"
    if has_compare and (has_rail or has_flight):
        return "transport_compare"
    if has_cross_city and has_rail and not has_nearby:
        return "rail_query"
    if has_cross_city and has_flight and not has_nearby:
        return "flight_query"
    if has_cross_city and has_nearby:
        return "trip_plan"
    if has_cross_city:
        return "trip_plan"
    if has_rail and not has_nearby:
        return "rail_query"
    if has_flight and not has_nearby:
        return "flight_query"
    if has_nearby and (has_rail or has_flight):
        return "trip_plan"
    if has_nearby:
        return "nearby_explore"
    return "trip_plan"


def _extract_cities(message: str) -> tuple[str, str]:
    route_match = re.search(r"从(.{1,8}?)到(.{1,8}?)(?:[\s，。,.\?？]|$)", message)
    if route_match:
        return route_match.group(1).strip(), route_match.group(2).strip()

    hits_with_pos = []
    for city in KNOWN_CITIES:
        index = message.find(city)
        if index >= 0:
            hits_with_pos.append((index, city))
    hits = [city for _index, city in sorted(hits_with_pos, key=lambda item: item[0])]
    if len(hits) >= 2:
        return hits[0], hits[1]
    if len(hits) == 1:
        return "", hits[0]

    route_match_alt = re.search(r"([^\s，。,.\?？]{1,8}?)到([^\s，。,.\?？]{1,8}?)(?:[\s，。,.\?？]|$)", message)
    if route_match_alt:
        return route_match_alt.group(1).strip(), route_match_alt.group(2).strip()
    return "", ""


def _extract_date(message: str) -> str:
    match = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})", message)
    if match:
        return match.group(1)

    now = datetime.now()
    if "明天" in message:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "后天" in message:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    if "下周" in message:
        return (now + timedelta(days=7)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def _build_flight_diagnostics(query: TravelQuery) -> list[str]:
    if query.travel_mode != "flight":
        return []

    airport_map = {"上海": "SHA", "杭州": "HGH", "北京": "BJS", "广州": "CAN", "深圳": "SZX", "湛江": "ZHA"}
    origin_code = airport_map.get(query.origin, query.origin)
    destination_code = airport_map.get(query.destination, query.destination)
    if not origin_code or not destination_code or not query.date:
        return []

    probe = probe_ctrip_flight_page(origin_code, destination_code, query.date)
    return [f"ctrip_probe ok={probe.ok} blocked={probe.blocked} message={probe.message} url={probe.url}"]


def build_travel_query(message: str, attachments: list[dict]) -> TravelQuery:
    origin, destination = _extract_cities(message)
    intent = _detect_intent(message, origin, destination)
    attachment_notes = extract_attachment_notes(attachments)
    named_places = extract_named_places(attachments, message=message)
    city = destination or origin

    travel_mode = ""
    if _contains_any(message, FLIGHT_KEYWORDS):
        travel_mode = "flight"
    elif _contains_any(message, RAIL_KEYWORDS):
        travel_mode = "rail"

    return TravelQuery(
        raw_text=message,
        intent=intent,
        origin=origin,
        destination=destination,
        city=city,
        date=_extract_date(message),
        preferences=[],
        travel_mode=travel_mode,
        attachment_notes=attachment_notes,
        named_places=named_places,
    )


def _should_include_explore(query: TravelQuery) -> bool:
    return query.intent in {"trip_plan", "nearby_explore", "trip_replan"}


def _should_include_weather(query: TravelQuery) -> bool:
    return _should_include_explore(query) or any(keyword in query.raw_text for keyword in ["天气", "下雨", "降温", "热不热"])


def _build_summary(query: TravelQuery) -> str:
    if query.intent == "nearby_explore":
        return f"已为 {query.destination or query.city or '目标地点'} 生成周边探索建议。"
    if query.intent == "transport_compare":
        return f"已整理 {query.origin or '出发地'} 到 {query.destination or '目的地'} 的出行方案，适合做第一轮交通比较。"
    if query.intent == "flight_query":
        return "已进入航班查询模式。"
    if query.intent == "rail_query":
        return "已进入高铁查询模式，优先返回 12306 方向的车次与座席信息。"
    if query.intent == "trip_replan":
        return f"已按照 {query.destination or query.city or '目的地'} 的重新规划场景生成一版新方案。"
    return f"已为 {query.destination or query.city or '目的地'} 生成一版出行与游玩结合的初步方案。"


def plan_travel(message: str, attachments: list[dict]) -> TravelPlanResponse:
    query = build_travel_query(message, attachments)
    has_cross_city_route = bool(query.origin and query.destination and query.origin != query.destination)

    should_fetch_transport = query.intent in {"rail_query", "flight_query", "transport_compare", "trip_plan", "trip_replan"}
    rail_options = []
    flight_options = []
    if should_fetch_transport:
        if query.intent == "flight_query" and query.travel_mode == "flight":
            flight_options = get_flight_options(query)
        elif query.intent == "rail_query" and query.travel_mode == "rail":
            rail_options = get_rail_options(query)
        else:
            rail_options = get_rail_options(query)
            flight_options = get_flight_options(query)
    transport_options = rail_options + flight_options

    include_explore = _should_include_explore(query)
    route_plans = get_route_plans(query) if include_explore else []
    poi_items, poi_groups = recommend_pois(query) if include_explore else ([], [])
    timeline = build_timeline(query, transport_options, route_plans, poi_items, poi_groups) if include_explore else []
    weather_summary = get_weather_summary(query.destination or query.city, forecast=True) if _should_include_weather(query) else ""
    diagnostics = _build_flight_diagnostics(query)

    alerts: list[str] = []
    if not rail_options and query.travel_mode == "rail":
        alerts.append("当前未获取到高铁实时结果，可能是站点、日期或 12306 查询受限。")
    if not flight_options and query.travel_mode == "flight":
        alerts.append("本次未获取到可用航班结果，可能是 FlightTicketMCP 查询超时、站点或机场参数不匹配，或该日期下暂无航班数据。")
    if has_cross_city_route and should_fetch_transport and not transport_options:
        alerts.append("当前识别到跨城出行需求，但暂未拿到高铁或航班候选结果，可继续补充更具体日期、城市站点或改查单一交通方式。")
    if has_cross_city_route and include_explore and not route_plans:
        alerts.append("当前为跨城出行场景，优先以高铁、航班等主交通方案为主，市内段路线建议会在确定酒店或具体目的地后生成。")
    if query.attachment_notes and include_explore:
        alerts.append("已结合附件线索生成初步方案，后续可继续细化为酒店、景点、会议联动行程。")

    return TravelPlanResponse(
        intent=query.intent,
        summary=_build_summary(query),
        transport_options=transport_options,
        route_plans=route_plans,
        timeline=timeline,
        poi_recommendations=poi_items,
        poi_groups=poi_groups,
        weather_summary=weather_summary,
        alerts=alerts,
        extracted_context=query.attachment_notes,
        diagnostics=diagnostics,
    )


def render_travel_response(response: TravelPlanResponse) -> str:
    lines = [response.summary]

    if response.extracted_context:
        lines.extend(["", "## 附件线索"])
        lines.extend([f"- {item}" for item in response.extracted_context])

    if response.transport_options:
        lines.extend(["", "## 交通方案"])
        for option in response.transport_options:
            lines.append(
                f"- [{option.mode}] {option.title} | {option.depart_time} -> {option.arrive_time} | "
                f"{option.duration or '时长待补充'} | {option.price or '价格待补充'}"
            )
            if option.summary:
                lines.append(f"  {option.summary}")
            if option.seats:
                lines.append(f"  座席/说明: {' / '.join(option.seats)}")

    if response.route_plans:
        lines.extend(["", "## 路线建议"])
        for route in response.route_plans:
            lines.append(
                f"- [{route.mode}] {route.origin} -> {route.destination} | "
                f"{route.duration or '时长待补充'} | {route.distance or '距离待补充'}"
            )
            if route.summary:
                lines.append(f"  {route.summary}")
            if route.origin_address:
                lines.append(f"  出发地：{route.origin_address}")
            if route.destination_address:
                lines.append(f"  目的地：{route.destination_address}")

    if response.timeline:
        lines.extend(["", "## 推荐行程"])
        for item in response.timeline:
            lines.append(f"- {item.time_label}: {item.title}")
            if item.detail:
                lines.append(f"  {item.detail}")

    if response.poi_recommendations:
        lines.extend(["", "## 周边探索"])
        for poi in response.poi_recommendations:
            label = f"{poi.name}（{poi.category}）"
            if poi.area:
                label += f" - {poi.area}"
            lines.append(f"- {label}")
            if poi.summary:
                lines.append(f"  {poi.summary}")

    if response.weather_summary:
        lines.extend(["", "## 天气参考", response.weather_summary])

    if response.alerts:
        lines.extend(["", "## 提醒"])
        lines.extend([f"- {item}" for item in response.alerts])

    return "\n".join(lines).strip()
