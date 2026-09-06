from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from adapters.flight_mcp_adapter import is_flight_mcp_enabled
from agents.schemas import (
    ClarificationOption,
    ClarificationRequest,
    Evidence,
    PlanDay,
    PlanItem,
    PoiGroup,
    PoiRecommendation,
    RoutePlan,
    TimelineItem,
    TransportPage,
    TransportOption,
    TravelConstraint,
    TravelFact,
    TravelPlan,
    TravelPlanResponse,
    TravelQuery,
)
from agents.travel_supervisor import TravelSupervisorResult
from services.flight_service import get_flight_options
from services.poi_recommender import PoiRecommendationResult, recommend_pois
from services.rail_service import get_rail_options
from services.route_service import get_route_plans
from services.trip_extractor import extract_attachment_notes, extract_named_places
from services.trip_planner import build_timeline
from services.transport_dates import is_valid_clock_time
from services.weather_service import get_weather_summary
from services.answer_citations import citation_marker


CN_TZ = ZoneInfo("Asia/Shanghai")
MAX_REQUESTED_TRIP_DAYS = 365

KNOWN_CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "苏州", "南京", "扬州", "无锡",
    "镇江", "常州", "南通", "徐州", "成都", "重庆", "武汉", "西安", "长沙",
    "厦门", "福州", "青岛", "济南", "郑州", "合肥", "南昌", "昆明", "贵阳",
    "湛江", "佛山",
]

# Province-level destinations need a different completeness rule from a
# city-level destination: five days in Jiangsu is not one place lookup. Keep
# the profile data-driven so other provinces can add route presets without
# changing the clarification pipeline.
PROVINCE_DESTINATION_PROFILES = {
    "江苏": {
        "aliases": ("江苏省", "江苏"),
        "cities": ("南京", "扬州", "苏州", "无锡", "镇江", "常州", "南通", "徐州"),
        "routes": (
            {
                "key": "A",
                "cities": ("南京", "扬州"),
                "label": "南京 + 扬州",
                "description": "历史人文、园林古城",
            },
            {
                "key": "B",
                "cities": ("苏州", "无锡"),
                "label": "苏州 + 无锡",
                "description": "水乡园林、休闲慢游",
            },
            {
                "key": "C",
                "cities": ("南京", "苏州"),
                "label": "南京 + 苏州",
                "description": "第一次去江苏的经典路线",
            },
        ),
    },
}

PROVINCE_ALIASES = {
    alias: province
    for province, profile in PROVINCE_DESTINATION_PROFILES.items()
    for alias in profile["aliases"]
}

RAIL_KEYWORDS = ["高铁", "火车", "动车", "12306", "车次", "车票", "余票"]
FLIGHT_KEYWORDS = ["飞机", "航班", "机票"]
NEARBY_KEYWORDS = ["附近", "周边", "景点", "好玩", "好吃", "餐厅", "咖啡馆", "推荐", "攻略", "网红", "热门", "打卡"]
REPLAN_KEYWORDS = [
    "重新规划", "重排", "改行程", "改路线", "调整行程", "调整路线", "下雨",
    "晚点", "不想走路", "少走路", "增加", "加上", "去掉", "删除", "预算变",
    "换成", "改成", "取消", "景点关闭",
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


def _has_cross_city_route(message: str, origin: str, destination: str) -> bool:
    if origin and destination and origin != destination:
        return True
    return bool(re.search(r"从.{1,12}到.{1,12}", message))


def _detect_intent(message: str, origin: str, destination: str) -> str:
    has_rail = _contains_any(message, RAIL_KEYWORDS)
    has_flight = _contains_any(message, FLIGHT_KEYWORDS)
    has_nearby = _contains_any(message, NEARBY_KEYWORDS)
    has_compare = _contains_any(message, ["对比", "比较", "哪个更好", "还是"])
    has_replan = _contains_any(message, REPLAN_KEYWORDS)
    has_cross_city = _has_cross_city_route(message, origin, destination)

    if has_replan:
        return "trip_replan"
    if has_compare and (has_rail or has_flight):
        return "transport_compare"
    if has_cross_city and has_rail and not has_nearby:
        return "rail_query"
    if has_cross_city and has_flight and not has_nearby:
        return "flight_query"
    if has_cross_city:
        return "trip_plan"
    if has_nearby and (has_rail or has_flight):
        return "trip_plan"
    if has_nearby:
        return "nearby_explore"
    return "trip_plan"


def _clean_location(value: str) -> str:
    cleaned = (value or "").strip(" \t，。,.；;：:")
    cleaned = re.sub(r"(?:玩|游玩|旅游|旅行|住|住宿|计划|安排).*$", "", cleaned).strip()
    return cleaned


def _extract_origin_hint(message: str) -> str:
    """Extract an optional origin from phrases such as ``从上海出发``."""

    match = re.search(r"(?:从|由)\s*([^\s，。,.;；]+?)\s*(?:出发|到|去|前往)", message)
    if not match:
        return ""
    candidate = _clean_location(match.group(1))
    for location in [*KNOWN_CITIES, *PROVINCE_ALIASES]:
        if location in candidate:
            return PROVINCE_ALIASES.get(location, location)
    return candidate


def _extract_cities(message: str) -> tuple[str, str]:
    # A province mention is the destination scope. It must win over city-name
    # scanning so ``江苏五日游，想去南京和苏州`` is not misread as a Shanghai-
    # style point-to-point route between the two cities.
    province_hits = [
        (message.find(alias), province)
        for alias, province in PROVINCE_ALIASES.items()
        if message.find(alias) >= 0
    ]
    if province_hits:
        _position, province = min(province_hits, key=lambda item: item[0])
        return _extract_origin_hint(message), province

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

    route_match = re.search(r"从\s*(.{1,12}?)\s*到\s*(.{1,18}?)(?:[，。,.;；\s]|$)", message)
    if route_match:
        return _clean_location(route_match.group(1)), _clean_location(route_match.group(2))

    route_match_alt = re.search(r"([^\s，。,.;；]{1,12}?)到([^\s，。,.;；]{1,18}?)(?:[\s，。,.;；]|$)", message)
    if route_match_alt:
        return _clean_location(route_match_alt.group(1)), _clean_location(route_match_alt.group(2))
    return "", ""


def _extract_destination_cities(message: str, destination: str) -> list[str]:
    province = PROVINCE_ALIASES.get(destination, destination)
    profile = PROVINCE_DESTINATION_PROFILES.get(province)
    if not profile:
        return []

    mentioned = [
        (message.find(city), city)
        for city in profile["cities"]
        if message.find(city) >= 0
    ]
    selected = [city for _position, city in sorted(mentioned, key=lambda item: item[0])]

    selection = re.search(r"(?:选择|选|路线)\s*([A-C])", message, re.IGNORECASE)
    if selection:
        route = next(
            (item for item in profile["routes"] if item["key"].upper() == selection.group(1).upper()),
            None,
        )
        if route:
            selected = list(route["cities"])

    return list(dict.fromkeys(selected))


_CN_NUMBERS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_number(value: str, default: int = 0) -> int:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    if text in _CN_NUMBERS:
        return _CN_NUMBERS[text]
    if len(text) == 2 and text[0] == "十":
        return 10 + _CN_NUMBERS.get(text[1], 0)
    if len(text) == 2 and text[1] == "十":
        return _CN_NUMBERS.get(text[0], 0) * 10
    if len(text) == 3 and text[1] == "十":
        return _CN_NUMBERS.get(text[0], 0) * 10 + _CN_NUMBERS.get(text[2], 0)
    return default


def _date_from_match(match: re.Match[str]) -> date | None:
    try:
        groups = match.groups()
        if len(groups) == 3:
            year, month, day = map(int, groups)
        else:
            month, day = map(int, groups)
            year = _now_cn().year
        value = date(year, month, day)
        if len(groups) == 2 and value < _now_cn().date() - timedelta(days=30):
            value = date(year + 1, month, day)
        return value
    except (TypeError, ValueError):
        return None


def _extract_explicit_dates(text: str) -> list[date]:
    patterns = [
        r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?",
        r"(\d{1,2})月(\d{1,2})日?",
    ]
    result: list[date] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = _date_from_match(match)
            if value and value not in result:
                result.append(value)
    return sorted(result)


def _extract_duration_days(text: str) -> tuple[int, bool]:
    match = re.search(
        r"(?<!月)(?<![-/])(?:玩|游玩|停留|旅行|旅游)?\s*(\d+|[一二两三四五六七八九十百]+)\s*[天日]",
        text,
    )
    if match:
        requested_days = max(1, _parse_number(match.group(1), 1))
        return min(MAX_REQUESTED_TRIP_DAYS, requested_days), requested_days > MAX_REQUESTED_TRIP_DAYS
    nights = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*晚", text)
    if nights:
        requested_days = max(1, _parse_number(nights.group(1), 1) + 1)
        return min(MAX_REQUESTED_TRIP_DAYS, requested_days), requested_days > MAX_REQUESTED_TRIP_DAYS
    return 1, False


def _extract_days(text: str) -> int:
    return _extract_duration_days(text)[0]


def _has_explicit_duration(text: str) -> bool:
    return bool(
        re.search(
            r"(?<!月)(?<![-/])(?:玩|游玩|停留|旅行|旅游)?\s*(\d+|[一二两三四五六七八九十百]+)\s*[天日]",
            text,
        )
        or re.search(r"(\d+|[一二两三四五六七八九十]+)\s*晚", text)
    )


def _extract_date_range(message: str) -> tuple[str, str, bool, int]:
    explicit_dates = _extract_explicit_dates(message)
    days = _extract_days(message)
    assumed = not bool(explicit_dates) and not _contains_any(message, ["明天", "后天", "下周", "今天"])
    today = _now_cn().date()
    if "后天" in message:
        start = today + timedelta(days=2)
        assumed = False
    elif "明天" in message:
        start = today + timedelta(days=1)
        assumed = False
    elif "下周" in message:
        start = today + timedelta(days=7)
        assumed = False
    elif explicit_dates:
        start = explicit_dates[0]
        if len(explicit_dates) >= 2:
            days = min(MAX_REQUESTED_TRIP_DAYS, max(1, (explicit_dates[-1] - start).days + 1))
    else:
        start = today
    end = start + timedelta(days=max(days, 1) - 1)
    return start.isoformat(), end.isoformat(), assumed, max(days, 1)


def _extract_travelers(message: str) -> int:
    match = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*(?:个|位)?\s*(?:人|位成人|大人)", message)
    if not match:
        return 1
    return max(1, min(30, _parse_number(match.group(1), 1)))


def _extract_preferences(message: str) -> list[str]:
    mapping = [
        ("亲子", ["亲子", "带孩子", "小朋友"]),
        ("美食", ["美食", "好吃", "吃货", "餐厅"]),
        ("咖啡", ["咖啡", "咖啡馆"]),
        ("拍照", ["拍照", "出片", "打卡"]),
        ("自然", ["自然", "山水", "公园"]),
        ("历史文化", ["历史", "古迹", "博物馆", "文化"]),
        ("夜游", ["夜景", "夜游", "晚上"]),
        ("慢游", ["慢游", "轻松", "不赶"]),
        ("少走路", ["不想走路", "少走路", "步行少"]),
    ]
    result = []
    for label, keywords in mapping:
        if any(keyword in message for keyword in keywords):
            result.append(label)
    return result


def _extract_constraints(message: str, preferences: list[str]) -> list[str]:
    constraints = []
    if "少走路" in preferences:
        constraints.append("步行距离尽量短")
    if _contains_any(message, ["预算", "便宜", "省钱"]):
        budget_match = re.search(r"预算[^0-9一二两三四五六七八九十百]*(\d+(?:\.\d+)?\s*[万千百元块]?)", message)
        constraints.append(f"预算 {budget_match.group(1)}" if budget_match else "控制预算")
    if _contains_any(message, ["不要早起", "不早起"]):
        constraints.append("避免过早出发")
    if _contains_any(message, ["必须", "一定"]):
        constraints.append("用户标记了硬性要求，需二次确认")
    return constraints


def _extract_date(message: str) -> str:
    return _extract_date_range(message)[0]


def _hint_string(hint: dict | None, key: str) -> str:
    value = (hint or {}).get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _hint_list(hint: dict | None, key: str) -> list[str]:
    value = (hint or {}).get(key, [])
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def build_travel_query(
    message: str,
    attachments: list[dict],
    *,
    extraction_hint: dict | None = None,
) -> TravelQuery:
    origin, destination = _extract_cities(message)
    if not origin:
        origin = _hint_string(extraction_hint, "origin")
    if not destination:
        destination = _hint_string(extraction_hint, "destination")
    origin = PROVINCE_ALIASES.get(origin, origin)
    destination = PROVINCE_ALIASES.get(destination, destination)
    intent = _detect_intent(message, origin, destination)
    hinted_intent = _hint_string(extraction_hint, "intent")
    if hinted_intent in {"rail_query", "flight_query", "transport_compare", "trip_plan", "nearby_explore", "trip_replan"}:
        # Deterministic routing wins whenever it found an explicit transport
        # request. The fallback may only fill the default trip intent.
        if intent == "trip_plan" and not _contains_any(message, [*RAIL_KEYWORDS, *FLIGHT_KEYWORDS]):
            intent = hinted_intent
    attachment_notes = extract_attachment_notes(attachments)
    named_places = extract_named_places(attachments, message=message)
    destination_scope = "province" if destination in PROVINCE_DESTINATION_PROFILES else "city" if destination else "unknown"
    destination_cities = _extract_destination_cities(message, destination)
    destination_cities = list(dict.fromkeys([*destination_cities, *_hint_list(extraction_hint, "destination_cities")]))
    if destination_scope == "province" and destination_cities:
        # City names serve as route/POI anchors after the province scope has
        # been clarified. Preserve any explicitly named landmarks after them.
        named_places = list(dict.fromkeys([*destination_cities, *named_places]))[:6]
    city = destination or origin
    start_date, end_date, date_is_assumed, days = _extract_date_range(message)
    explicit_dates = _extract_explicit_dates(message)
    hinted_start_date = _hint_string(extraction_hint, "start_date")
    if not explicit_dates and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", hinted_start_date):
        try:
            hinted_date = date.fromisoformat(hinted_start_date)
        except ValueError:
            hinted_date = None
        if hinted_date is not None:
            start_date = hinted_date.isoformat()
            date_is_assumed = False
    _duration_days, duration_was_capped = _extract_duration_days(message)
    hinted_days = (extraction_hint or {}).get("days")
    if not _has_explicit_duration(message) and isinstance(hinted_days, int) and hinted_days > 0:
        days = min(MAX_REQUESTED_TRIP_DAYS, hinted_days)
        duration_was_capped = hinted_days > MAX_REQUESTED_TRIP_DAYS
        end_date = (date.fromisoformat(start_date) + timedelta(days=days - 1)).isoformat()
    preferences = _extract_preferences(message)
    preferences = list(dict.fromkeys([*preferences, *_hint_list(extraction_hint, "preferences")]))

    travel_mode = ""
    if _contains_any(message, FLIGHT_KEYWORDS):
        travel_mode = "flight"
    elif _contains_any(message, RAIL_KEYWORDS):
        travel_mode = "rail"
    elif _hint_string(extraction_hint, "travel_mode") in {"flight", "rail"}:
        travel_mode = _hint_string(extraction_hint, "travel_mode")

    travelers = _extract_travelers(message)
    hinted_travelers = (extraction_hint or {}).get("travelers")
    if travelers == 1 and isinstance(hinted_travelers, int) and 1 < hinted_travelers <= 30 and "人" not in message:
        travelers = hinted_travelers

    query = TravelQuery(
        raw_text=message,
        intent=intent,
        origin=origin,
        destination=destination,
        city=city,
        date=start_date,
        start_date=start_date,
        end_date=end_date,
        days=days,
        travelers=travelers,
        travel_mode=travel_mode,
        preferences=preferences,
        constraints=_extract_constraints(message, preferences),
        duration_is_assumed=not _has_explicit_duration(message) and not isinstance(hinted_days, int),
        duration_was_capped=duration_was_capped
        or (
            len(explicit_dates) >= 2
            and (explicit_dates[-1] - explicit_dates[0]).days + 1 > MAX_REQUESTED_TRIP_DAYS
        ),
        attachment_notes=attachment_notes,
        named_places=named_places,
        destination_scope=destination_scope,
        destination_cities=destination_cities,
    )
    query.date_is_assumed = date_is_assumed
    return query


def _inherit_previous_requirement(query: TravelQuery, current_plan: TravelPlan | None) -> TravelQuery:
    if current_plan is None:
        return query
    if not query.origin:
        query.origin = current_plan.origin
    if not query.destination:
        query.destination = current_plan.destination
    if query.destination_scope == "unknown":
        query.destination_scope = current_plan.destination_scope
    if not query.destination_cities:
        query.destination_cities = list(current_plan.destination_cities)
    query.city = query.destination or query.city or current_plan.destination
    if not query.start_date or query.date_is_assumed:
        query.start_date = current_plan.start_date
        query.date = current_plan.start_date
        if query.duration_is_assumed:
            query.end_date = current_plan.end_date
            query.days = max(1, (date.fromisoformat(query.end_date) - date.fromisoformat(query.start_date)).days + 1)
        else:
            query.end_date = (
                date.fromisoformat(query.start_date) + timedelta(days=max(query.days, 1) - 1)
            ).isoformat()
        query.date_is_assumed = False
    if not query.preferences:
        query.preferences = list(current_plan.preferences)
    if query.travelers == 1 and current_plan.travelers > 1 and "人" not in query.raw_text:
        query.travelers = current_plan.travelers
    inherited_constraints = [constraint.label for constraint in current_plan.constraints]
    query.constraints = list(dict.fromkeys([*inherited_constraints, *query.constraints]))
    return query


def prepare_travel_query(
    message: str,
    attachments: list[dict],
    *,
    current_plan: TravelPlan | None = None,
    extraction_hint: dict | None = None,
) -> TravelQuery:
    """Build the normalized requirement used by both planner and supervisor."""

    return _inherit_previous_requirement(
        build_travel_query(message, attachments, extraction_hint=extraction_hint),
        current_plan,
    )


def _should_include_explore(query: TravelQuery) -> bool:
    return query.intent in {"trip_plan", "nearby_explore", "trip_replan"}


def _has_effective_travel_data(
    query: TravelQuery,
    transport_options: list[TransportOption],
    route_plans: list[RoutePlan],
    poi_items: list[PoiRecommendation],
    weather_summary: str,
) -> bool:
    """Return whether provider-backed facts are sufficient for a plan."""

    if any(_is_effective_transport_option(option) for option in transport_options):
        return True
    if any(len(route.polyline) >= 2 for route in route_plans):
        return True
    if any(item.source_ids and not item.is_placeholder for item in poi_items):
        return True
    # Weather is useful context, but by itself cannot support a concrete
    # itinerary. Require a transport result, route geometry, or a verified
    # place before creating a TravelPlan.
    return False


def _is_effective_transport_option(option: TransportOption) -> bool:
    """Reject placeholder transport objects before they can create a plan."""

    return bool(
        _is_well_formed_transport_option(option)
        and not option.is_demo
    )


def _is_well_formed_transport_option(option: TransportOption) -> bool:
    return bool(
        str(option.title or "").strip()
        and str(option.provider or "").strip()
        and is_valid_clock_time(option.depart_time)
        and is_valid_clock_time(option.arrive_time)
    )


def _provider_retry_info(adapter_status: dict[str, str]) -> tuple[bool, str]:
    """Return a user-facing retry hint for transient provider failures."""

    labels = {
        "rail": "铁路",
        "flight": "航班",
        "route": "地图路线",
        "poi": "目的地地点",
        "weather": "天气",
        "web_search": "联网搜索",
    }
    failed = [labels[key] for key, status in adapter_status.items() if status == "failed" and key in labels]
    if not failed:
        return False, ""
    return True, f"{'、'.join(failed)}数据源暂时不可用，可以点击重试。"


def _plan_is_explicitly_requested(query: TravelQuery) -> bool:
    """Avoid turning a plain discovery/query turn into a saved itinerary."""

    if query.intent == "trip_replan":
        return True
    if query.intent in {"rail_query", "flight_query", "transport_compare"} and not any(
        token in str(query.raw_text or "")
        for token in ("规划", "安排", "行程", "攻略", "游玩")
    ):
        return False
    return any(
        token in str(query.raw_text or "")
        for token in ("规划", "安排", "生成行程", "制定行程", "行程安排", "定制行程", "设计行程", "几日游", "日游")
    ) or bool(
        re.search(
            r"(?:去|到).{0,20}(?:玩|游玩).{0,8}(?:\d+|[一二两三四五六七八九十]+)\s*[天日]",
            str(query.raw_text or ""),
        )
    )


NO_PROVIDER_DATA_NOTICE = "本次没有拿到可用于规划的有效旅行数据，暂不生成行程计划。"


def _route_anchor_names_from_recommendations(
    recommendations: list[PoiRecommendation],
) -> list[str]:
    """Choose map-verified sightseeing anchors for a generic city trip."""

    route_categories = ("景点", "去处", "公园", "风景", "博物馆", "古迹")
    names: list[str] = []
    for recommendation in recommendations:
        if recommendation.is_placeholder:
            continue
        if not any(token in recommendation.category for token in route_categories):
            continue
        name = recommendation.name.strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 4:
            break
    return names


def _transport_page_from_result(mode: str, result, query: TravelQuery) -> TransportPage:
    high_speed = mode == "rail" and any(
        token in str(query.raw_text or "")
        for token in ("高铁", "动车", "G字头", "D字头", "C字头")
    )
    returned_count = len(result) if isinstance(result, (list, tuple)) else 0
    return TransportPage(
        mode=mode,
        offset=max(0, int(getattr(result, "offset", 0))),
        limit=max(1, int(getattr(result, "limit", 5))),
        returned_count=returned_count,
        total_count=max(returned_count, int(getattr(result, "total_count", returned_count))),
        has_more=bool(getattr(result, "has_more", False)),
        filter="high_speed" if high_speed else "all",
    )


def _build_clarification(query: TravelQuery) -> ClarificationRequest | None:
    if not query.destination:
        return ClarificationRequest(
            code="destination_required",
            prompt="我还没有识别出目的地。请告诉我想去哪个城市或省份。",
        )

    if query.destination_scope != "province":
        return None

    province = PROVINCE_ALIASES.get(query.destination, query.destination)
    profile = PROVINCE_DESTINATION_PROFILES.get(province)
    if not profile or query.destination_cities:
        return None

    options = [
        ClarificationOption(
            key=route["key"],
            label=route["label"],
            description=route["description"],
            value=f"选择 {route['key']}：{route['label']}",
        )
        for route in profile["routes"]
    ]
    options.append(
        ClarificationOption(
            key="D",
            label="自定义城市",
            description="直接告诉我想去的城市",
            value="",
        )
    )
    return ClarificationRequest(
        code="destination_cities",
        prompt=(
            f"{province}范围比较大，{query.days}天建议选择 2～3 个城市。"
            "你更想走哪条路线？预计出发日期也可以一并告诉我；日期未定也可以先做行程。"
        ),
        options=options,
    )


def _should_include_weather(query: TravelQuery) -> bool:
    return _should_include_explore(query) or any(keyword in query.raw_text for keyword in ["天气", "下雨", "降温", "热不热"])


def _build_summary(query: TravelQuery) -> str:
    destination = query.destination or query.city or "目的地"
    if query.intent == "nearby_explore":
        return f"已为 {destination} 生成周边探索建议，并整理成可加入日历的候选安排。"
    if query.intent == "transport_compare":
        return f"已整理 {query.origin or '出发地'} 到 {destination} 的出行方案，适合做第一轮交通比较。"
    if query.intent == "flight_query":
        return "已进入航班查询模式。"
    if query.intent == "rail_query":
        return "已进入高铁查询模式，优先返回 12306 方向的车次与座席信息。"
    if query.intent == "trip_replan":
        return f"已按照你的新要求重新规划 {destination} 的行程，并保留已锁定安排。"
    return f"已为 {destination} 生成一版出行与游玩结合的初步方案。"


def _transport_evidence(options: list[TransportOption]) -> list[Evidence]:
    result = []
    for index, option in enumerate(options, start=1):
        provider = option.provider or "unknown"
        source_type = "rail_realtime" if option.mode == "rail" else "flight_realtime"
        evidence_id = f"transport_{index:03d}_{option.mode}"
        option.source_ids = [evidence_id]
        result.append(
            Evidence(
                evidence_id=evidence_id,
                source_type=source_type,
                provider=provider,
                title=option.title,
                url=(
                    "https://kyfw.12306.cn/"
                    if option.mode == "rail"
                    else "https://mcp.variflight.com/"
                    if provider.lower() == "variflight"
                    else ""
                ),
                snippet=(
                    f"{option.depart_date or ''} {option.depart_time} -> "
                    f"{option.arrive_date or ''} {option.arrive_time} {option.duration}"
                ).strip(),
                retrieved_at=_now_cn().isoformat(timespec="seconds"),
                freshness="current_query",
                reliability="adapter_result",
                supports=["transport_candidate"],
                is_demo=option.is_demo,
            )
        )
    return result


def _flight_failure_detail(exc: Exception) -> str:
    """Expose safe provider diagnostics without copying response bodies or keys."""

    parts = [f"flight adapter failed: {type(exc).__name__}"]
    failure_kind = str(getattr(exc, "failure_kind", "") or "")
    http_status = getattr(exc, "http_status", 0)
    provider_code = str(getattr(exc, "provider_code", "") or "")
    if failure_kind:
        parts.append(f"kind={failure_kind}")
    if http_status:
        parts.append(f"http_status={http_status}")
    if provider_code:
        parts.append(f"provider_code={provider_code}")
    return " ".join(parts)


def _route_evidence(routes: list[RoutePlan]) -> list[Evidence]:
    return [
        Evidence(
            evidence_id=f"route_{index:03d}",
            source_type="map_route",
            provider="Amap",
            title=f"{route.origin} -> {route.destination}",
            url="https://www.amap.com/",
            snippet=f"{route.mode} {route.duration} {route.distance}".strip(),
            retrieved_at=_now_cn().isoformat(timespec="seconds"),
            freshness="current_query",
            reliability="primary_adapter",
            supports=["route", "duration", "distance"],
        )
        for index, route in enumerate(routes, start=1)
    ]


def _stable_item_id(item_type: str, title: str, day: str) -> str:
    raw = f"{item_type}|{title}|{day}".encode("utf-8", errors="ignore")
    return f"item_{hashlib.sha1(raw).hexdigest()[:12]}"


def _new_plan_item(
    *,
    item_type: str,
    title: str,
    day: str,
    detail: str = "",
    start_time: str = "",
    end_time: str = "",
    location: str = "",
    address: str = "",
    source_ids: list[str] | None = None,
    estimated_cost: str = "",
    confidence: str = "unknown",
    end_date: str = "",
    is_demo: bool = False,
    seat_count: int | None = None,
) -> PlanItem:
    return PlanItem(
        item_id=_stable_item_id(item_type, title, day),
        item_type=item_type,
        title=title,
        date=day,
        start_time=start_time,
        end_time=end_time,
        location=location,
        address=address,
        detail=detail,
        source_ids=list(dict.fromkeys(source_ids or [])),
        estimated_cost=estimated_cost,
        confidence=confidence,
        end_date=end_date,
        is_demo=is_demo,
        seat_count=seat_count,
    )


def _date_sequence(start_date: str, end_date: str) -> list[str]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return [start_date]
    if end < start:
        return [start.isoformat()]
    span = min((end - start).days + 1, 31)
    return [(start + timedelta(days=index)).isoformat() for index in range(span)]


def _ensure_unique_item_ids(items: list[PlanItem]) -> None:
    """Keep PATCH addressing unambiguous when providers repeat a title."""

    seen: dict[str, int] = {}
    assigned: set[str] = set()
    for item in items:
        base_id = item.item_id or _stable_item_id(item.item_type, item.title, item.date)
        occurrence = seen.get(base_id, 0)
        candidate = base_id if occurrence == 0 else f"{base_id}_{occurrence + 1}"
        while candidate in assigned:
            occurrence += 1
            candidate = f"{base_id}_{occurrence + 1}"
        seen[base_id] = occurrence + 1
        assigned.add(candidate)
        item.item_id = candidate


def _build_structured_plan(
    query: TravelQuery,
    *,
    thread_id: str,
    search_enabled: bool,
    transport_options: list[TransportOption],
    transport_pages: list[TransportPage],
    route_plans: list[RoutePlan],
    poi_items: list[PoiRecommendation],
    poi_groups: list[PoiGroup],
    sources: list[Evidence],
    conflicts: list[str],
    risks: list[str],
    alerts: list[str],
    diagnostics: list[str],
    adapter_status: dict[str, str],
    current_plan: TravelPlan | None,
) -> TravelPlan:
    day_dates = _date_sequence(query.start_date, query.end_date)
    days = [PlanDay(date=value, day_number=index + 1) for index, value in enumerate(day_dates)]
    out_of_range_items: list[PlanItem] = []

    if transport_options and days:
        option = transport_options[0]
        transport_date = option.depart_date or query.start_date or days[0].date
        target_day = next((day for day in days if day.date == transport_date), days[0])
        arrival_date = option.arrive_date or transport_date
        transport_source_ids = [
            item.evidence_id
            for item in sources
            if item.source_type.endswith("realtime") and item.title == option.title
        ]
        target_day.items.append(
            _new_plan_item(
                item_type="transport",
                title=f"前往 {query.destination or '目的地'}：{option.title}",
                day=target_day.date,
                start_time=option.depart_time,
                end_time=option.arrive_time,
                detail=f"{option.mode}；{option.duration or '时长待补充'}；{option.summary or ''}".strip("；"),
                source_ids=transport_source_ids,
                estimated_cost=option.price,
                confidence="source_backed",
                end_date=arrival_date if arrival_date != target_day.date else "",
                is_demo=option.is_demo,
                seat_count=option.seat_count,
            )
        )
        if arrival_date != target_day.date:
            arrival_day = next((day for day in days if day.date == arrival_date), None)
            if arrival_day is None:
                risks.append("跨日交通的抵达日期超出当前日历展开范围，已保留在出发日的跨日项目中。")
            else:
                arrival_day.items.append(
                    _new_plan_item(
                        item_type="transport_arrival",
                        title=f"抵达 {query.destination or '目的地'}：{option.title}",
                        day=arrival_date,
                        start_time=option.arrive_time,
                        detail=f"由 {option.title} 抵达；请以实际到站信息为准。",
                        source_ids=transport_source_ids,
                        confidence="source_backed",
                        is_demo=option.is_demo,
                    )
                )

    for index, route in enumerate(route_plans):
        day = days[min(index, len(days) - 1)]
        day.items.append(
            _new_plan_item(
                item_type="route",
                title=f"{route.origin} -> {route.destination}",
                day=day.date,
                detail=f"{route.mode}，约 {route.duration or '时长待补充'}，{route.distance or '距离待补充'}。{route.summary}".strip(),
                location=route.destination,
                address=route.destination_address,
                source_ids=[item.evidence_id for item in sources if item.source_type == "map_route" and item.title == f"{route.origin} -> {route.destination}"],
                confidence="source_backed",
            )
        )

    for index, poi in enumerate(poi_items[: min(16, max(4, len(days) * 4))]):
        day = days[index % len(days)]
        item_type = "food" if any(token in poi.category for token in ["餐", "美食", "咖啡"]) else "attraction"
        detail_parts = [poi.summary]
        if poi.popularity_signal:
            detail_parts.append(poi.popularity_signal)
        day.items.append(
            _new_plan_item(
                item_type=item_type,
                title=poi.name,
                day=day.date,
                detail="；".join(part for part in detail_parts if part),
                location=poi.name,
                address=poi.address,
                source_ids=poi.source_ids,
                estimated_cost=poi.estimated_cost,
                confidence="source_backed" if poi.source_ids else "unverified",
            )
        )

    locked_items: list[PlanItem] = []
    conflict_dates: set[str] = set()
    if current_plan:
        locked_items = [
            item
            for plan_day in current_plan.days
            for item in plan_day.items
            if item.locked or item.status in {"confirmed", "booked"}
        ]
        locked_items.extend(
            item
            for item in current_plan.out_of_range_items
            if item.locked or item.status in {"confirmed", "booked"}
        )
    for item in locked_items:
        if item.date not in {day.date for day in days}:
            conflicts.append(f"已锁定项目“{item.title}”不在新的日期范围内，未自动移动。")
            out_of_range_items.append(deepcopy(item))
            if days:
                conflict_dates.add(days[0].date if item.date < days[0].date else days[-1].date)
            continue
        target_day = next(day for day in days if day.date == item.date)
        existing_index = next(
            (index for index, existing in enumerate(target_day.items) if existing.item_id == item.item_id),
            None,
        )
        if existing_index is None:
            target_day.items.append(deepcopy(item))
        else:
            # The freshly generated suggestion may have the same stable ID
            # as an existing locked item.  The locked snapshot is authoritative
            # and must win over the new suggestion in every field.
            target_day.items[existing_index] = deepcopy(item)

    for day in days:
        day.items.sort(key=lambda item: (item.start_time or "99:99", item.item_type, item.title))
        titles = [item.title for item in day.items]
        day.title = f"第 {day.day_number} 天"
        day.summary = "、".join(titles[:4]) if titles else "暂无已确认的具体安排，可继续告诉我想去的地方。"
        day.has_conflicts = day.date in conflict_dates

    _ensure_unique_item_ids([item for day in days for item in day.items] + out_of_range_items)

    facts = [
        TravelFact(
            fact_id=f"fact_attachment_{index}",
            fact_type="attachment_clue",
            label="附件线索",
            value=value,
            confirmed=False,
            notes="来自用户附件，需用户确认后才视为锁定事实。",
        )
        for index, value in enumerate(query.attachment_notes, start=1)
    ]
    constraints = [
        TravelConstraint(
            constraint_id=f"constraint_{index}",
            kind="preference" if not any(token in item for token in ["预算", "必须"]) else "requirement",
            label=item,
            value=item,
            hard="必须" in item,
            satisfied=None,
        )
        for index, item in enumerate(query.constraints, start=1)
    ]
    if query.date_is_assumed:
        risks.append("未明确出发日期，当前按今天起算；请确认日期后再查看交通方案。")
    if query.duration_was_capped:
        risks.append("日期范围超过 365 天，当前计划已截取前 365 天；请缩小范围后再细化行程。")
    if len(day_dates) >= 31 and query.end_date > day_dates[-1]:
        risks.append("行程超过 31 天，日历暂只展开前 31 天。")

    return TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date=day_dates[0] if day_dates else query.start_date,
        # Keep the requested range even when the calendar projection is
        # intentionally capped at 31 expanded days.
        end_date=query.end_date or (day_dates[-1] if day_dates else ""),
        requested_days=max(1, query.days),
        projected_days=len(day_dates),
        calendar_truncated=bool(query.end_date and day_dates and query.end_date > day_dates[-1]),
        projection_end_date=day_dates[-1] if day_dates else query.start_date,
        origin=query.origin,
        destination=query.destination or query.city,
        destination_scope=query.destination_scope,
        destination_cities=list(query.destination_cities),
        travelers=query.travelers,
        preferences=list(query.preferences),
        summary=_build_summary(query),
        days=days,
        transport_options=list(transport_options),
        transport_pages=list(transport_pages),
        route_plans=list(route_plans),
        out_of_range_items=out_of_range_items,
        facts=facts,
        constraints=constraints,
        conflicts=list(dict.fromkeys(conflicts)),
        risks=list(dict.fromkeys(risks)),
        alerts=list(dict.fromkeys(alerts)),
        diagnostics=list(dict.fromkeys(diagnostics)),
        adapter_status=dict(adapter_status),
        sources=list(dict((item.evidence_id, item) for item in sources).values()),
        search_enabled=search_enabled,
        status="needs_attention" if conflicts or risks or alerts or diagnostics else "draft",
    )


def plan_travel(
    message: str,
    attachments: list[dict],
    *,
    thread_id: str = "",
    search_enabled: bool = False,
    current_plan: TravelPlan | None = None,
    activity_logger=None,
    extraction_hint: dict | None = None,
    supervisor_result: TravelSupervisorResult | None = None,
    cancellation_check=None,
) -> TravelPlanResponse:
    def check_cancelled() -> None:
        if cancellation_check:
            cancellation_check()

    check_cancelled()
    query = (
        supervisor_result.query
        if supervisor_result is not None and not supervisor_result.fallback_used
        else prepare_travel_query(
            message,
            attachments,
            current_plan=current_plan,
            extraction_hint=extraction_hint,
        )
    )
    check_cancelled()
    clarification = _build_clarification(query)
    if clarification is not None:
        # Do not create a placeholder TravelPlan or spend provider quota while
        # a blocking destination requirement is still ambiguous.
        return TravelPlanResponse(
            intent=query.intent,
            summary="我可以帮你规划这趟旅行，但需要先确认一个关键信息。",
            clarification=clarification,
            pending_query=asdict(query),
            decision="clarify",
            decision_reason="旅行需求缺少可执行的目的地范围。",
        )
    model_decision = (
        supervisor_result.decision
        if supervisor_result is not None and not supervisor_result.fallback_used
        else ""
    )
    if model_decision not in {"answer", "plan", "clarify", "refuse"}:
        # Direct calls and a failed Supervisor retain the deterministic
        # planner's historical default. A live Supervisor must provide an
        # explicit action before it can influence plan creation.
        model_decision = "plan"
    plan_requested = _plan_is_explicitly_requested(query)
    if model_decision == "plan" and not plan_requested:
        model_decision = "answer"
    has_cross_city_route = bool(query.origin and query.destination and query.origin != query.destination)

    should_fetch_transport = query.intent in {"rail_query", "flight_query", "transport_compare", "trip_plan", "trip_replan"}
    if query.intent == "transport_compare":
        # An explicit comparison is the one case where both providers are
        # intentionally queried.
        rail_requested = should_fetch_transport
        flight_requested = should_fetch_transport
    elif query.intent == "rail_query" or query.travel_mode == "rail":
        rail_requested = should_fetch_transport
        flight_requested = False
    elif query.intent == "flight_query" or query.travel_mode == "flight":
        rail_requested = False
        flight_requested = should_fetch_transport
    else:
        # A general trip plan may benefit from both transport options.
        rail_requested = should_fetch_transport
        flight_requested = should_fetch_transport
    rail_options: list[TransportOption] = []
    flight_options: list[TransportOption] = []
    transport_pages: list[TransportPage] = []
    alerts: list[str] = []
    risks: list[str] = []
    conflicts: list[str] = []
    diagnostics: list[str] = []
    adapter_status: dict[str, str] = {}

    if supervisor_result is not None and not supervisor_result.fallback_used:
        rail_options = [item for item in supervisor_result.transport_options if item.mode == "rail"]
        flight_options = [item for item in supervisor_result.transport_options if item.mode == "flight"]
        transport_pages = list(supervisor_result.transport_pages)
        route_plans = list(supervisor_result.route_plans)
        poi_items = list(supervisor_result.poi_items)
        poi_groups = list(supervisor_result.poi_groups)
        poi_sources = list(supervisor_result.sources)
        poi_errors = []
        weather_summary = supervisor_result.weather_summary
        adapter_status.update(supervisor_result.adapter_status)
        diagnostics.extend(
            list(dict.fromkeys([*supervisor_result.errors, *supervisor_result.diagnostics]))
        )
        if supervisor_result.fallback_used:
            diagnostics.append("Supervisor 未完成工具决策，已回退确定性旅行规划。")
    elif rail_requested and query.origin and query.destination and query.date:
        try:
            rail_result = get_rail_options(query)
            rail_options = list(rail_result)
            transport_pages.append(_transport_page_from_result("rail", rail_result, query))
            rail_errors = list(getattr(rail_result, "errors", []) or [])
            if rail_errors:
                adapter_status["rail"] = "partial" if rail_options else "failed"
                diagnostics.extend(f"rail row failed: {error}" for error in rail_errors)
                if rail_options:
                    alerts.append("部分火车结果格式异常，已保留仍可用的车次候选。")
            else:
                adapter_status["rail"] = "success" if rail_options else "empty"
        except Exception as exc:
            adapter_status["rail"] = "failed"
            diagnostics.append(f"rail adapter failed: {type(exc).__name__}")
            alerts.append("高铁数据接口暂时失败，本次没有把交通时间当作已确认事实。")
        check_cancelled()
    else:
        adapter_status["rail"] = "not_requested"

    if supervisor_result is not None and not supervisor_result.fallback_used:
        pass
    elif flight_requested and query.origin and query.destination and query.date:
        try:
            flight_result = get_flight_options(query)
            flight_options = list(flight_result)
            transport_pages.append(_transport_page_from_result("flight", flight_result, query))
            flight_errors = list(getattr(flight_result, "errors", []) or [])
            if flight_errors:
                adapter_status["flight"] = "partial" if flight_options else "failed"
                diagnostics.extend(f"flight row failed: {error}" for error in flight_errors)
                if flight_options:
                    alerts.append("部分航班结果格式异常，已保留仍可用的航班候选。")
            else:
                adapter_status["flight"] = (
                    "success"
                    if flight_options
                    else "not_configured"
                    if not is_flight_mcp_enabled()
                    else "empty"
                )
        except Exception as exc:
            adapter_status["flight"] = "failed"
            diagnostics.append(_flight_failure_detail(exc))
            alerts.append("航班数据接口暂时失败，本次没有把交通时间当作已确认事实。")
        check_cancelled()
    elif supervisor_result is None or supervisor_result.fallback_used:
        adapter_status["flight"] = "not_requested"
    rail_options = [item for item in rail_options if _is_well_formed_transport_option(item)]
    flight_options = [item for item in flight_options if _is_well_formed_transport_option(item)]
    if supervisor_result is not None and not supervisor_result.fallback_used:
        # The supervisor has already made the provider calls. Keep only the
        # selected mode results and do not call adapters a second time.
        transport_options = rail_options + flight_options
    else:
        transport_options = rail_options + flight_options

    include_explore = _should_include_explore(query) or (
        model_decision == "plan" and plan_requested
    )
    if supervisor_result is not None and not supervisor_result.fallback_used:
        if include_explore:
            has_route_geometry = any(len(route.polyline) >= 2 for route in route_plans)
            if not has_route_geometry and not query.named_places:
                route_anchor_names = _route_anchor_names_from_recommendations(poi_items)
                if len(route_anchor_names) >= 2:
                    route_query = deepcopy(query)
                    route_query.named_places = route_anchor_names
                    # A model-led run may choose POI without choosing a route.
                    # Do not silently make another provider call here; the
                    # supervisor owns tool selection for this turn.
            web_search_status = adapter_status.get("web_search", "not_requested")
        else:
            route_plans = []
            poi_items, poi_groups, poi_sources, poi_errors = [], [], [], []
            web_search_status = "not_requested"
    elif include_explore:
        check_cancelled()
        try:
            route_result = get_route_plans(query)
            route_plans = list(route_result)
            route_errors = list(getattr(route_result, "errors", []) or [])
            if route_errors:
                adapter_status["route"] = "partial" if route_plans else "failed"
                diagnostics.extend(f"route mode failed: {error}" for error in route_errors)
                alerts.append(
                    "部分地图路线模式查询失败，已保留仍可用的路线结果。"
                    if route_plans
                    else "地图路线模式查询失败，本次没有可用路线结果。"
                )
            else:
                adapter_status["route"] = "success" if route_plans else "empty"
        except Exception as exc:
            route_plans = []
            adapter_status["route"] = "failed"
            diagnostics.append(f"route adapter failed: {type(exc).__name__}")
            alerts.append("地图路线接口暂时失败，市内移动时间需要到现场再确认。")
        check_cancelled()
        try:
            poi_result = recommend_pois(
                query, search_enabled=search_enabled, activity_logger=activity_logger
            )
            if isinstance(poi_result, PoiRecommendationResult):
                poi_items = poi_result.recommendations
                poi_groups = poi_result.groups
                poi_sources = poi_result.evidence
                poi_errors = poi_result.errors
                poi_status = poi_result.poi_status
                web_search_status = poi_result.web_search_status
            else:
                poi_items, poi_groups, poi_sources, poi_errors = poi_result
                poi_status = "success" if poi_items else ("failed" if poi_errors else "empty")
                web_search_status = "disabled" if not search_enabled else ("failed" if poi_errors else "empty")
        except Exception as exc:
            poi_items, poi_groups, poi_sources = [], [], []
            poi_errors = ["地图 POI 接口失败，未生成未经验证的地点。"]
            poi_status = "failed"
            web_search_status = "disabled" if not search_enabled else "failed"
            adapter_status["poi"] = "failed"
            diagnostics.append(f"poi adapter failed: {type(exc).__name__}")
        else:
            adapter_status["poi"] = poi_status
        check_cancelled()

        # A generic city request such as "广州五日游" has no explicit
        # landmark list, so the first route lookup quite correctly returns
        # no segments. Reuse the map-backed sightseeing recommendations as
        # route anchors after POI discovery; explicit user landmarks remain
        # authoritative and are never silently replaced.
        has_route_geometry = any(len(route.polyline) >= 2 for route in route_plans)
        if not has_route_geometry and not query.named_places:
            route_anchor_names = _route_anchor_names_from_recommendations(poi_items)
            if len(route_anchor_names) >= 2:
                route_query = deepcopy(query)
                route_query.named_places = route_anchor_names
                try:
                    fallback_route_result = get_route_plans(route_query)
                    route_plans = list(fallback_route_result)
                    fallback_route_errors = list(getattr(fallback_route_result, "errors", []) or [])
                    if fallback_route_errors:
                        adapter_status["route"] = "partial" if route_plans else "failed"
                        diagnostics.extend(
                            f"route mode failed: {error}" for error in fallback_route_errors
                        )
                        alerts.append(
                            "部分地图路线模式查询失败，已保留仍可用的路线结果。"
                            if route_plans
                            else "地图路线模式查询失败，本次没有可用路线结果。"
                        )
                    else:
                        adapter_status["route"] = "success" if route_plans else "empty"
                except Exception as exc:
                    route_plans = []
                    adapter_status["route"] = "failed"
                    diagnostics.append(f"route adapter failed: {type(exc).__name__}")
                    alerts.append("地图路线接口暂时失败，市内移动时间需要到现场再确认。")
                check_cancelled()
    else:
        route_plans = []
        poi_items, poi_groups, poi_sources, poi_errors = [], [], [], []
        web_search_status = "not_requested"
        adapter_status["route"] = "not_requested"
        adapter_status["poi"] = "not_requested"

    weather_requested = _should_include_weather(query)
    if supervisor_result is not None and not supervisor_result.fallback_used:
        adapter_status.setdefault("weather", "success" if weather_summary else "not_requested")
    elif weather_requested:
        try:
            weather_summary = get_weather_summary(query.destination or query.city, forecast=True)
            adapter_status["weather"] = "success" if weather_summary else "empty"
        except Exception as exc:
            weather_summary = ""
            adapter_status["weather"] = "failed"
            diagnostics.append(f"weather adapter failed: {type(exc).__name__}")
            alerts.append("天气接口暂时失败，行程中的天气判断需要重新查询。")
        check_cancelled()
    else:
        weather_summary = ""
        adapter_status["weather"] = "not_requested"

    adapter_status["web_search"] = "not_requested" if not include_explore else web_search_status
    retryable, retry_reason = _provider_retry_info(adapter_status)
    risks.extend(poi_errors)
    check_cancelled()
    timeline = build_timeline(query, transport_options, route_plans, poi_items, poi_groups) if include_explore else []
    if any(option.is_demo for option in transport_options):
        risks.append("当前交通列表含演示数据，只用于联调展示，不可用于购票或判断真实班次。")
    if not rail_options and query.travel_mode == "rail":
        alerts.append("当前未获取到高铁实时结果，可能是站点、日期或 12306 查询受限。")
    if not flight_options and query.travel_mode == "flight":
        if adapter_status.get("flight") == "not_configured":
            alerts.append("航班查询能力尚未启用，本次没有执行真实航班查询。")
        else:
            alerts.append("本次未获取到可用航班结果；请把它视为未查询到，不是无航班或可预订结果。")
    if has_cross_city_route and should_fetch_transport and not transport_options:
        risks.append("已识别到跨城出行，但暂未拿到高铁或航班候选，不能把交通时间当作已确认事实。")
    if has_cross_city_route and include_explore and not route_plans:
        risks.append("市内路线暂未取得，建议确认酒店或具体目的地后再细化。")
    if query.attachment_notes and include_explore:
        alerts.append("已结合附件线索生成初步方案；附件中的票据/预约信息仍需你确认后锁定。")
    if search_enabled and poi_errors:
        alerts.append("联网搜索部分失败或候选未通过地图验证，已保留可验证的地图结果。")

    sources = _transport_evidence(transport_options) + _route_evidence(route_plans) + poi_sources
    if weather_summary:
        sources.append(
            Evidence(
                evidence_id="weather_001",
                source_type="weather",
                provider="Amap",
                title=f"{query.destination or query.city} 天气参考",
                url="https://www.amap.com/",
                snippet=weather_summary[:500],
                retrieved_at=_now_cn().isoformat(timespec="seconds"),
                freshness="current_query",
                reliability="primary_adapter",
                supports=["weather_context"],
            )
        )

    if model_decision == "refuse":
        check_cancelled()
        return TravelPlanResponse(
            intent=query.intent,
            summary=(supervisor_result.answer if supervisor_result else "抱歉，这个请求不在旅行规划服务范围内。"),
            transport_options=transport_options,
            route_plans=route_plans,
            poi_recommendations=poi_items,
            poi_groups=poi_groups,
            weather_summary=weather_summary,
            alerts=alerts,
            diagnostics=diagnostics,
            adapter_status=adapter_status,
            sources=sources,
            decision="refuse",
            decision_reason=(supervisor_result.decision_reason if supervisor_result else "模型未授权生成旅行计划。"),
            scope_refusal=True,
            retryable=False,
        )

    if model_decision == "clarify":
        check_cancelled()
        model_clarification = ClarificationRequest(
            code="model_clarification",
            prompt=(
                supervisor_result.answer.strip()
                if supervisor_result is not None and supervisor_result.answer.strip()
                else "我还需要补充出发地、日期或其他旅行条件，才能继续处理。"
            ),
        )
        return TravelPlanResponse(
            intent=query.intent,
            summary="我还需要补充一些旅行条件，才能继续处理。",
            transport_options=transport_options,
            route_plans=route_plans,
            transport_pages=transport_pages,
            poi_recommendations=poi_items,
            poi_groups=poi_groups,
            weather_summary=weather_summary,
            alerts=alerts,
            diagnostics=diagnostics,
            adapter_status=adapter_status,
            sources=sources,
            clarification=model_clarification,
            pending_query=asdict(query),
            decision="clarify",
            decision_reason=(supervisor_result.decision_reason if supervisor_result else "旅行条件仍不完整。"),
            retryable=False,
        )

    if model_decision == "answer" or (
        supervisor_result is None and not _plan_is_explicitly_requested(query)
    ):
        check_cancelled()
        has_effective_data = _has_effective_travel_data(
            query, transport_options, route_plans, poi_items, weather_summary
        )
        if not has_effective_data:
            alerts.extend(item for item in poi_errors if item not in alerts)
            alerts.append(NO_PROVIDER_DATA_NOTICE)
        answer = (
            supervisor_result.answer
            if supervisor_result is not None and supervisor_result.answer
            else _build_summary(query)
        )
        return TravelPlanResponse(
            intent=query.intent,
            summary=answer,
            transport_options=transport_options,
            route_plans=route_plans,
            transport_pages=transport_pages,
            poi_recommendations=poi_items,
            poi_groups=poi_groups,
            weather_summary=weather_summary,
            alerts=alerts,
            extracted_context=query.attachment_notes,
            diagnostics=diagnostics,
            adapter_status=adapter_status,
            sources=sources,
            decision="answer",
            decision_reason=(supervisor_result.decision_reason if supervisor_result else "本轮未明确要求生成行程。"),
            retryable=retryable,
            retry_reason=retry_reason,
        )

    if not _has_effective_travel_data(query, transport_options, route_plans, poi_items, weather_summary):
        check_cancelled()
        alerts.extend(item for item in poi_errors if item not in alerts)
        alerts.append(NO_PROVIDER_DATA_NOTICE)
        no_data_summary = (
            supervisor_result.answer.strip()
            if supervisor_result is not None and supervisor_result.answer.strip()
            else ""
        )
        if no_data_summary and NO_PROVIDER_DATA_NOTICE not in no_data_summary:
            no_data_summary = f"{no_data_summary}\n\n{NO_PROVIDER_DATA_NOTICE}"
        return TravelPlanResponse(
            intent=query.intent,
            summary=no_data_summary or NO_PROVIDER_DATA_NOTICE,
            transport_options=transport_options,
            route_plans=route_plans,
            transport_pages=transport_pages,
            poi_recommendations=poi_items,
            poi_groups=poi_groups,
            weather_summary=weather_summary,
            alerts=alerts,
            extracted_context=query.attachment_notes,
            diagnostics=diagnostics,
            adapter_status=adapter_status,
            sources=sources,
            decision="answer",
            decision_reason="没有有效 provider 数据。",
            retryable=retryable,
            retry_reason=retry_reason,
        )

    check_cancelled()
    plan = _build_structured_plan(
        query,
        thread_id=thread_id,
        search_enabled=search_enabled,
        transport_options=transport_options,
        transport_pages=transport_pages,
        route_plans=route_plans,
        poi_items=poi_items,
        poi_groups=poi_groups,
        sources=sources,
        conflicts=conflicts,
        risks=risks,
        alerts=alerts,
        diagnostics=diagnostics,
        adapter_status=adapter_status,
        current_plan=current_plan,
    )

    check_cancelled()
    return TravelPlanResponse(
        intent=query.intent,
        summary=_build_summary(query),
        transport_options=transport_options,
        route_plans=route_plans,
        transport_pages=transport_pages,
        timeline=timeline,
        poi_recommendations=poi_items,
        poi_groups=poi_groups,
        weather_summary=weather_summary,
        alerts=alerts,
        extracted_context=query.attachment_notes,
        diagnostics=diagnostics,
        adapter_status=adapter_status,
        trip_plan=plan,
        sources=plan.sources,
        conflicts=plan.conflicts,
        decision="plan",
        decision_reason=(supervisor_result.decision_reason if supervisor_result else "已明确提出行程规划需求，且取得有效数据。"),
        retryable=retryable,
        retry_reason=retry_reason,
    )


def render_travel_response(response: TravelPlanResponse) -> str:
    """Render a compact conversational summary for persisted plans.

    The structured plan panel is the canonical home for full route, ticket,
    POI, and weather details. Chat should explain what is ready and point the
    user there instead of duplicating a raw provider dump.
    """

    if response.trip_plan is not None:
        plan = response.trip_plan
        lines = [response.summary]
        lines.append(
            f"计划日期：{plan.start_date} 至 {plan.end_date}（第 {plan.version or 1} 版草案）"
        )

        if plan.transport_pages:
            for page in plan.transport_pages:
                label = "车次" if page.mode == "rail" else "航班" if page.mode == "flight" else "交通候选"
                lines.append(
                    f"已查询到 {page.total_count or page.returned_count} 条{label}，当前先展示 {page.returned_count} 条。"
                )
        elif response.transport_options:
            mode_counts: dict[str, int] = {}
            for option in response.transport_options:
                mode_counts[option.mode] = mode_counts.get(option.mode, 0) + 1
            label = "、".join(
                f"{count} 条{'车次' if mode == 'rail' else '航班' if mode == 'flight' else '交通'}"
                for mode, count in mode_counts.items()
            )
            lines.append(f"已查询到 {label}。")
        route_count = sum(1 for route in response.route_plans if len(route.polyline) >= 2)
        if route_count:
            lines.append(f"已生成 {route_count} 段地图路线。")
        if response.poi_recommendations:
            lines.append(f"已整理 {len(response.poi_recommendations)} 个已验证地点。")
        if response.weather_summary:
            lines.append("已附加目的地天气参考。")
        if response.alerts or plan.risks or plan.conflicts:
            issue_count = len(response.alerts) + len(plan.risks) + len(plan.conflicts)
            lines.append(f"当前有 {issue_count} 条提醒或风险，请在“状态”中查看详情。")

        lines.append("详细行程已同步到行程计划面板，可查看每日安排、路线地图和数据来源。")
        return "\n\n".join(lines).strip()

    return _render_detailed_travel_response(response)


def _render_detailed_travel_response(response: TravelPlanResponse) -> str:
    lines = [response.summary]
    if response.clarification:
        lines.extend(["", response.clarification.prompt])
        return "\n".join(lines).strip()
    web_source_ids = {
        source.evidence_id
        for source in response.sources
        if source.source_type == "web_search" and citation_marker(source.evidence_id)
    }
    if response.trip_plan:
        lines.append(
            f"\n计划日期：{response.trip_plan.start_date} 至 {response.trip_plan.end_date}"
            f"（第 {response.trip_plan.version or 1} 版草案）"
        )

    if response.extracted_context:
        lines.extend(["", "## 附件线索"])
        lines.extend([f"- {item}" for item in response.extracted_context])

    if response.transport_options:
        lines.extend(["", "## 交通方案"])
        for option in response.transport_options:
            lines.append(
                f"- [{'演示' if option.is_demo else option.mode}] {option.title} | "
                f"{option.depart_date or ''} {option.depart_time} -> {option.arrive_date or ''} {option.arrive_time} | "
                f"{option.duration or '时长待补充'} | {option.price or '价格待补充'}"
            )
            if option.summary:
                lines.append(f"  {option.summary}")
            if option.seats:
                if option.seat_count is not None:
                    lines.append(f"  provider seat count: {option.seat_count} (informational; not held)")
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
            web_markers = ""
            if poi.rating:
                label += f" 评分 {poi.rating}"
            if poi.popularity_signal:
                web_markers = "".join(
                    citation_marker(source_id)
                    for source_id in poi.source_ids
                    if source_id in web_source_ids
                )
            if poi.area:
                label += f" - {poi.area}"
            lines.append(f"- {label}")
            if poi.popularity_signal:
                popularity_line = f"  {poi.popularity_signal}"
                if web_markers:
                    popularity_line += f"。{web_markers}"
                lines.append(popularity_line)
            if poi.summary:
                lines.append(f"  {poi.summary}")

    if response.weather_summary:
        lines.extend(["", "## 天气参考", response.weather_summary])

    if response.alerts:
        lines.extend(["", "## 提醒"])
        lines.extend([f"- {item}" for item in response.alerts])
    if response.trip_plan and response.trip_plan.risks:
        lines.extend(["", "## 计划风险"])
        lines.extend([f"- {item}" for item in response.trip_plan.risks])
    if response.trip_plan and response.trip_plan.conflicts:
        lines.extend(["", "## 计划冲突"])
        lines.extend([f"- {item}" for item in response.trip_plan.conflicts])

    return "\n".join(lines).strip()
