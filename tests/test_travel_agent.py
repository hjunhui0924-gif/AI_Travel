from agents import travel_agent
from adapters.variflight_adapter import VariFlightError
from agents.schemas import (
    Evidence,
    ClarificationRequest,
    PlanDay,
    PlanItem,
    PoiRecommendation,
    RoutePlan,
    TransportOption,
    TravelPlan,
    TravelPlanResponse,
)
from services import poi_recommender
from services.rail_service import RailOptionsResult
from agents.travel_supervisor import TravelSupervisorResult


def _fake_recommend(query, *, search_enabled=False, activity_logger=None):
    poi = PoiRecommendation(
        name="西湖边餐厅",
        category="美食",
        address="湖滨路",
        rating="4.8",
        rating_source="Amap",
        source_ids=["amap_food"],
        summary="评分 4.8（高德）",
    )
    source = Evidence(
        evidence_id="amap_food",
        source_type="map_poi",
        provider="Amap",
        title=poi.name,
        retrieved_at="2026-08-09T10:00:00+08:00",
        reliability="primary_adapter",
    )
    return [poi], [], [source], []


def test_calendar_date_is_not_mistaken_for_trip_duration():
    query = travel_agent.build_travel_query("9月2日去杭州", [])

    assert query.start_date.endswith("-09-02")
    assert query.days == 1
    assert query.duration_is_assumed is True


def test_explicit_place_list_is_split_into_route_anchors():
    query = travel_agent.build_travel_query(
        "2026年9月2日到9月4日去杭州，包含西湖、灵隐寺和河坊街，慢节奏。",
        [],
    )

    assert query.named_places[:3] == ["西湖", "灵隐寺", "河坊街"]


def test_explicit_place_list_stops_before_following_preference_clause():
    query = travel_agent.build_travel_query(
        "2026-10-01 从北京到上海玩三天，坐飞机，想去外滩、豫园和陆家嘴，安排本帮菜。",
        [],
    )

    assert query.named_places == ["外滩", "豫园", "陆家嘴"]


def test_confirming_one_place_preserves_other_requested_landmarks():
    query = travel_agent.prepare_travel_query(
        "2026-10-01 从北京到上海玩三天，坐飞机，想去外滩、豫园和陆家嘴，安排本帮菜。\n用户补充：place_outer",
        [],
        extraction_hint={
            "confirmed_place": {
                "candidate_id": "place_outer",
                "provider_id": "place_outer",
                "name": "外滩",
                "query_text": "外滩",
                "city": "上海",
            },
            "unresolved_places": ["外滩", "豫园", "陆家嘴"],
        },
    )

    assert query.named_places == ["外滩", "豫园", "陆家嘴"]


def test_place_confirmation_hint_accumulates_previous_confirmations():
    query = travel_agent.prepare_travel_query(
        "2026-10-01 从北京到上海玩三天，想去外滩、豫园和陆家嘴。",
        [],
        extraction_hint={
            "confirmed_place": {
                "candidate_id": "garden",
                "provider_id": "garden",
                "name": "上海豫园",
                "query_text": "豫园",
            },
            "resolved_place_candidates": [
                {
                    "candidate_id": "bund",
                    "provider_id": "bund",
                    "name": "外滩",
                    "query_text": "外滩",
                }
            ],
            "unresolved_places": ["豫园", "陆家嘴"],
        },
    )

    assert [item.name for item in query.resolved_place_candidates] == ["外滩", "上海豫园"]


def test_province_trip_asks_for_city_route_before_calling_external_adapters(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(travel_agent, "recommend_pois", lambda *args, **kwargs: calls.append("poi"))
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda *args, **kwargs: calls.append("route"))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda *args, **kwargs: calls.append("rail"))
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda *args, **kwargs: calls.append("flight"))
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda *args, **kwargs: calls.append("weather"))

    response = travel_agent.plan_travel("江苏五日游", [], search_enabled=False)

    assert response.trip_plan is None
    assert response.clarification is not None
    assert response.clarification.code == "destination_cities"
    assert response.clarification.options
    assert "江苏" in response.clarification.prompt
    assert calls == []


def test_province_route_choice_becomes_city_anchors():
    query = travel_agent.build_travel_query("江苏五日游，选择 A：南京 + 扬州", [])

    assert query.destination == "江苏"
    assert query.destination_scope == "province"
    assert query.destination_cities == ["南京", "扬州"]
    assert query.named_places[:2] == ["南京", "扬州"]


def test_clarification_response_renders_choices_without_a_placeholder_plan():
    response = travel_agent.plan_travel("江苏五日游", [], search_enabled=False)

    rendered = travel_agent.render_travel_response(response)

    assert response.trip_plan is None
    assert response.summary in rendered
    assert "江苏范围比较大" not in rendered
    assert response.clarification is not None
    assert response.clarification.options[0].label == "南京 + 扬州"
    assert response.clarification.options[1].label == "苏州 + 无锡"
    assert "出发地待定" not in rendered


def test_place_clarification_prompt_is_owned_by_the_structured_card():
    response = TravelPlanResponse(
        intent="trip_plan",
        summary="请先确认具体地点，再继续计算路线和营业时间。",
        clarification=ClarificationRequest(
            code="place_ambiguous",
            prompt="“外滩”有多个候选，请确认你要去哪个地点：",
        ),
    )

    rendered = travel_agent.render_travel_response(response)

    assert response.summary in rendered
    assert response.clarification.prompt not in rendered


def test_route_coverage_requires_every_explicit_adjacent_pair():
    routes = [
        RoutePlan(
            mode="driving",
            origin="外滩",
            destination="豫园",
            polyline=[[121.49, 31.23], [121.50, 31.22]],
        ),
    ]

    assert travel_agent._route_covers_named_places(routes, ["外滩", "豫园", "陆家嘴"]) is False
    routes.append(
        RoutePlan(
            mode="driving",
            origin="豫园",
            destination="陆家嘴",
            polyline=[[121.50, 31.22], [121.51, 31.24]],
        )
    )
    assert travel_agent._route_covers_named_places(routes, ["外滩", "豫园", "陆家嘴"]) is True


def test_completed_plan_response_is_a_compact_summary_not_a_raw_data_dump():
    response = TravelPlanResponse(
        intent="trip_plan",
        summary="已为杭州生成一版出行与游玩结合的初步方案。",
        transport_options=[
            TransportOption(mode="rail", title="G123", provider="12306"),
            TransportOption(mode="rail", title="G456", provider="12306"),
        ],
        route_plans=[
            RoutePlan(
                mode="driving",
                origin="西湖",
                destination="灵隐寺",
                origin_address="不应在聊天摘要中展开的长地址",
                destination_address="不应在聊天摘要中展开的长地址",
                polyline=[[120.0, 30.0], [120.1, 30.1]],
            )
        ],
        poi_recommendations=[
            PoiRecommendation(name="西湖", category="景点"),
            PoiRecommendation(name="灵隐寺", category="景点"),
        ],
        weather_summary="天气详细数据应在结构化区域查看",
        trip_plan=TravelPlan(
            plan_id="plan_summary",
            thread_id="guest_summary",
            version=1,
            timezone="Asia/Shanghai",
            start_date="2026-09-02",
            end_date="2026-09-04",
            destination="杭州",
            days=[],
        ),
    )

    rendered = travel_agent.render_travel_response(response)

    assert "已为杭州生成一版出行与游玩结合的初步方案。" in rendered
    assert "2026-09-02 至 2026-09-04" in rendered
    assert "2 条车次" in rendered
    assert "1 段地图路线" in rendered
    assert "2 个已验证地点" in rendered
    assert "详细行程已同步到行程计划面板" in rendered
    assert "不应在聊天摘要中展开的长地址" not in rendered
    assert "天气详细数据应在结构化区域查看" not in rendered


def test_clarified_province_route_waits_for_verified_data_before_creating_plan(monkeypatch):
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], []))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel(
        "江苏五日游\n用户补充：选择 A，从上海出发",
        [],
        search_enabled=False,
    )

    assert response.clarification is None
    assert response.trip_plan is None
    assert response.decision == "answer"
    assert "暂不生成行程计划" in response.summary


def test_explicit_date_range_is_capped_with_structured_risk():
    query = travel_agent.build_travel_query("2026-01-01 到 2028-01-01 去杭州", [])

    assert query.days == travel_agent.MAX_REQUESTED_TRIP_DAYS
    assert query.end_date == "2026-12-31"
    assert query.duration_was_capped is True


def test_explicit_duration_is_capped_with_structured_risk():
    query = travel_agent.build_travel_query("2026-01-01 去杭州玩 400 天", [])

    assert query.days == travel_agent.MAX_REQUESTED_TRIP_DAYS
    assert query.duration_was_capped is True


def test_plan_builds_calendar_dates_and_uses_search_switch(monkeypatch):
    calls = []
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: calls.append(kwargs["search_enabled"]) or _fake_recommend(query, **kwargs))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel(
        "2026-09-02 从上海去杭州玩三天，两个人，喜欢美食，不想走路",
        [],
        thread_id="guest_agent_test",
        search_enabled=False,
    )

    assert calls == [False]
    assert response.trip_plan.start_date == "2026-09-02"
    assert response.trip_plan.end_date == "2026-09-04"
    assert response.trip_plan.travelers == 2
    assert len(response.trip_plan.days) == 3
    assert any("步行" in item.label for item in response.trip_plan.constraints)
    assert response.trip_plan.days[0].items[0].title == "西湖边餐厅"


def test_transport_query_only_calls_the_requested_provider(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_rail(query):
        calls.append(("rail", query.travel_mode))
        return []

    def fake_flight(query):
        calls.append(("flight", query.travel_mode))
        return []

    monkeypatch.setattr(travel_agent, "get_rail_options", fake_rail)
    monkeypatch.setattr(travel_agent, "get_flight_options", fake_flight)

    travel_agent.plan_travel("2026-09-02 从上海到杭州查高铁", [], search_enabled=False)

    assert calls == [("rail", "rail")]


def test_flight_query_only_calls_the_flight_provider(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_rail(query):
        calls.append(("rail", query.travel_mode))
        return []

    def fake_flight(query):
        calls.append(("flight", query.travel_mode))
        return []

    monkeypatch.setattr(travel_agent, "get_rail_options", fake_rail)
    monkeypatch.setattr(travel_agent, "get_flight_options", fake_flight)

    travel_agent.plan_travel("2026-09-02 从上海到杭州查航班", [], search_enabled=False)

    assert calls == [("flight", "flight")]


def test_plan_uses_supervisor_provider_results_without_calling_adapters(monkeypatch):
    query = travel_agent.build_travel_query("2026-09-02 从上海到杭州查高铁", [])
    option = TransportOption(
        mode="rail",
        title="G123",
        provider="12306",
        depart_date="2026-09-02",
        arrive_date="2026-09-02",
        depart_time="08:00",
        arrive_time="09:00",
    )
    supervised = TravelSupervisorResult(
        query=query,
        tool_calls=[],
        transport_options=[option],
        transport_pages=[
            travel_agent.TransportPage(
                mode="rail",
                returned_count=1,
                total_count=6,
                has_more=True,
                filter="high_speed",
            )
        ],
        adapter_status={
            "rail": "success",
            "flight": "not_requested",
            "route": "not_requested",
            "poi": "not_requested",
            "weather": "not_requested",
            "web_search": "not_requested",
        },
    )
    monkeypatch.setattr(
        travel_agent,
        "get_rail_options",
        lambda query: (_ for _ in ()).throw(AssertionError("rail adapter must not run twice")),
    )
    monkeypatch.setattr(
        travel_agent,
        "get_flight_options",
        lambda query: (_ for _ in ()).throw(AssertionError("flight adapter must not run")),
    )

    response = travel_agent.plan_travel(
        "2026-09-02 从上海到杭州查高铁",
        [],
        supervisor_result=supervised,
    )

    assert response.trip_plan is None
    assert response.decision == "answer"
    assert [item.title for item in response.transport_options] == ["G123"]
    assert response.transport_pages[0].has_more is True


def test_model_answer_decision_never_creates_itinerary_from_transport_data():
    query = travel_agent.build_travel_query("2026-09-02 从上海到杭州查高铁", [])
    supervised = TravelSupervisorResult(
        query=query,
        decision="answer",
        answer="已查到车次，请在结果中选择。",
        transport_options=[TransportOption(mode="rail", title="G123", provider="12306")],
    )

    response = travel_agent.plan_travel(
        "2026-09-02 从上海到杭州查高铁",
        [],
        supervisor_result=supervised,
    )

    assert response.trip_plan is None
    assert response.decision == "answer"
    assert response.summary == "已查到车次，请在结果中选择。"


def test_model_plan_decision_is_blocked_when_no_provider_data_exists():
    query = travel_agent.build_travel_query("2026-09-02 去杭州玩三天", [])
    supervised = TravelSupervisorResult(query=query, decision="plan")

    response = travel_agent.plan_travel(
        "2026-09-02 去杭州玩三天",
        [],
        supervisor_result=supervised,
    )

    assert response.trip_plan is None
    assert response.decision == "answer"
    assert "暂不生成行程计划" in response.summary


def test_model_plan_decision_rejects_placeholder_transport_data():
    query = travel_agent.build_travel_query("2026-09-02 去杭州玩三天", [])
    supervised = TravelSupervisorResult(
        query=query,
        decision="plan",
        transport_options=[TransportOption(mode="rail", title="车次")],
    )

    response = travel_agent.plan_travel(
        "2026-09-02 去杭州玩三天",
        [],
        supervisor_result=supervised,
    )

    assert response.trip_plan is None
    assert travel_agent.NO_PROVIDER_DATA_NOTICE in response.summary


def test_generic_city_trip_builds_route_from_verified_poi_recommendations(monkeypatch):
    attractions = [
        PoiRecommendation(name="广州塔", category="景点", address="阅江西路", summary="高德景点", source_ids=["poi_1"]),
        PoiRecommendation(name="北京路", category="景点", address="北京路", summary="高德景点", source_ids=["poi_2"]),
    ]
    route_calls: list[list[str]] = []

    def fake_route(query):
        route_calls.append(list(query.named_places))
        if query.named_places != ["广州塔", "北京路"]:
            return []
        return [
            RoutePlan(
                mode="driving",
                origin="广州塔",
                destination="北京路",
                origin_location="113.3302,23.1135",
                destination_location="113.2708,23.1259",
                polyline=[[113.3302, 23.1135], [113.2708, 23.1259]],
            )
        ]

    monkeypatch.setattr(travel_agent, "get_route_plans", fake_route)
    monkeypatch.setattr(
        travel_agent,
        "recommend_pois",
        lambda query, **kwargs: (attractions, [], [], []),
    )
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel("2026-09-01 去广州玩五天", [], search_enabled=False)

    assert route_calls == [[], ["广州塔", "北京路"]]
    assert response.trip_plan is not None
    assert len(response.trip_plan.route_plans) == 1
    assert response.trip_plan.route_plans[0].polyline


def test_replan_preserves_locked_items_and_reports_date_conflict(monkeypatch):
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], []))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")
    current = TravelPlan(
        plan_id="plan_existing",
        thread_id="guest_replan_test",
        version=1,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-03",
        destination="杭州",
        days=[
            PlanDay(
                date="2026-09-02",
                day_number=1,
                items=[PlanItem("locked-1", "hotel", "已确认酒店", "2026-09-02", status="confirmed", locked=True)],
            ),
            PlanDay(
                date="2026-09-03",
                day_number=2,
                items=[PlanItem("locked-2", "ticket", "已确认演出", "2026-09-03", status="confirmed", locked=True)],
            ),
        ],
    )

    response = travel_agent.plan_travel(
        "改成只玩一天",
        [],
        thread_id="guest_replan_test",
        search_enabled=False,
        current_plan=current,
    )

    assert response.trip_plan is None
    assert response.decision == "answer"
    assert "暂不生成行程计划" in response.summary


def test_no_map_result_does_not_create_placeholder_poi(monkeypatch):
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], ["地图接口失败"]))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel("杭州附近有什么好吃的", [], search_enabled=False)

    assert response.poi_recommendations == []
    assert "地图接口失败" in response.alerts
    assert travel_agent.NO_PROVIDER_DATA_NOTICE in response.alerts


def test_poi_partial_failure_keeps_successful_anchor_results(monkeypatch):
    query = travel_agent.build_travel_query("杭州的西湖附近好吃的", [])
    monkeypatch.setattr(poi_recommender, "has_amap_key", lambda: True)
    monkeypatch.setattr(
        poi_recommender,
        "resolve_place_in_city",
        lambda city, place: {"name": "西湖", "location": "120,30", "address": "湖边"},
    )
    monkeypatch.setattr(
        poi_recommender,
        "search_pois_around_location",
        lambda location, **kwargs: (
            [{"name": "湖边餐厅", "address": "湖边", "rating": "4.6"}]
            if kwargs.get("types") == poi_recommender.FOOD_TYPES
            else (_ for _ in ()).throw(TimeoutError("nearby timeout"))
        ),
    )
    monkeypatch.setattr(poi_recommender, "search_pois", lambda *args, **kwargs: [])
    monkeypatch.setattr(poi_recommender, "discover_travel_places", lambda *args, **kwargs: type("R", (), {
        "sources": [], "errors": [], "candidates": [], "status": "disabled"
    })())

    result = poi_recommender.recommend_pois(query, search_enabled=False)

    assert result.recommendations[0].name == "湖边餐厅"
    assert result.poi_status == "partial"
    assert any("附近去处查询失败" in error for error in result.errors)


def test_poi_web_discovery_failure_keeps_map_results(monkeypatch):
    query = travel_agent.build_travel_query("杭州的西湖附近好吃的", [])
    monkeypatch.setattr(poi_recommender, "has_amap_key", lambda: True)
    monkeypatch.setattr(
        poi_recommender,
        "resolve_place_in_city",
        lambda city, place: {"name": "西湖", "location": "120,30", "address": "湖边"},
    )
    monkeypatch.setattr(
        poi_recommender,
        "search_pois_around_location",
        lambda location, **kwargs: (
            [{"name": "湖边餐厅", "address": "湖边", "rating": "4.6"}]
            if kwargs.get("types") == poi_recommender.FOOD_TYPES
            else []
        ),
    )
    monkeypatch.setattr(poi_recommender, "search_pois", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        poi_recommender,
        "discover_travel_places",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("search client init failed")),
    )

    result = poi_recommender.recommend_pois(query, search_enabled=True)

    assert [item.name for item in result.recommendations] == ["湖边餐厅"]
    assert result.poi_status == "success"
    assert result.web_search_status == "failed"
    assert any("旅行网页搜索失败" in error for error in result.errors)


def test_overnight_transport_keeps_arrival_date_on_plan_item(monkeypatch):
    overnight = TransportOption(
        mode="rail",
        title="G123",
        depart_date="2026-09-02",
        arrive_date="2026-09-03",
        depart_time="23:10",
        arrive_time="01:20",
        duration="2小时10分",
        provider="12306",
        seat_count=6,
    )
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], []))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [overnight])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel(
        "2026-09-02 从上海去杭州玩两天",
        [],
        thread_id="guest_overnight_test",
        search_enabled=False,
    )

    item = response.trip_plan.days[0].items[0]
    assert item.date == "2026-09-02"
    assert item.end_date == "2026-09-03"
    assert item.seat_count == 6
    assert response.trip_plan.days[1].items[0].item_type == "transport_arrival"
    assert response.trip_plan.days[1].items[0].start_time == "01:20"
    assert response.timeline[0].date == "2026-09-02"
    assert response.timeline[0].end_date == "2026-09-03"


def test_flight_failure_detail_keeps_provider_diagnostics_structured():
    error = VariFlightError(
        "provider rejected request",
        failure_kind="unauthorized",
        http_status=401,
        provider_code="AUTH001",
    )

    detail = travel_agent._flight_failure_detail(error)

    assert "kind=unauthorized" in detail
    assert "http_status=401" in detail
    assert "provider_code=AUTH001" in detail
    assert "provider rejected request" not in detail


def test_replan_replaces_same_id_suggestion_with_locked_snapshot(monkeypatch):
    locked_id = travel_agent._stable_item_id("attraction", "西湖", "2026-09-02")
    current = TravelPlan(
        plan_id="plan_locked_collision",
        thread_id="guest_locked_collision",
        version=1,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[
            PlanDay(
                date="2026-09-02",
                day_number=1,
                items=[
                    PlanItem(
                        locked_id,
                        "attraction",
                        "西湖",
                        "2026-09-02",
                        detail="用户已确认的安排",
                        status="confirmed",
                        locked=True,
                    )
                ],
            )
        ],
    )
    poi = PoiRecommendation(name="西湖", category="景点", summary="新生成的建议", source_ids=["new"])
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([poi], [], [], []))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel(
        "重新规划杭州行程",
        [],
        thread_id="guest_locked_collision",
        search_enabled=False,
        current_plan=current,
    )

    item = response.trip_plan.days[0].items[0]
    assert item.item_id == locked_id
    assert item.locked is True
    assert item.status == "confirmed"
    assert item.detail == "用户已确认的安排"


def test_repeated_same_day_poi_titles_get_distinct_item_ids(monkeypatch):
    poi = PoiRecommendation(name="同名餐厅", category="美食", source_ids=["poi_same"])
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([poi, poi], [], [], []))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel("2026-09-02 去杭州玩一天", [], search_enabled=False)
    items = response.trip_plan.days[0].items

    assert [item.title for item in items] == ["同名餐厅", "同名餐厅"]
    assert len({item.item_id for item in items}) == 2


def test_adapter_failure_is_structured_in_plan(monkeypatch):
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])

    response = travel_agent.plan_travel("2026-09-02 从上海去杭州坐飞机", [], search_enabled=False)

    assert response.trip_plan is None
    assert response.adapter_status["flight"] == "failed"
    assert response.diagnostics == ["flight adapter failed: TimeoutError"]
    assert any("航班数据接口暂时失败" in item for item in response.alerts)
    assert response.retryable is True
    assert "航班" in response.retry_reason


def test_partial_transport_result_is_exposed_in_plan(monkeypatch):
    rail_result = RailOptionsResult(
        [TransportOption(mode="rail", title="G123")],
        errors=["row:dict"],
    )
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: rail_result)
    # Keep this unit test deterministic even when a developer's .env enables
    # a live flight provider for integration checks.
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])

    response = travel_agent.plan_travel("2026-09-02 从上海去杭州坐高铁", [], search_enabled=False)

    assert response.trip_plan is None
    assert response.adapter_status["rail"] == "partial"
    assert response.diagnostics == ["rail row failed: row:dict"]
    assert any("部分火车结果" in item for item in response.alerts)


def test_long_trip_keeps_requested_end_date_and_marks_calendar_projection(monkeypatch):
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], []))
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel("2026-09-01 去杭州玩 32 天", [], search_enabled=False)

    assert response.trip_plan is None
    assert response.decision == "answer"
    assert "暂不生成行程计划" in response.summary
