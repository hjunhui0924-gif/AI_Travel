from agents import travel_agent
from agents.schemas import Evidence, PlanDay, PlanItem, PoiRecommendation, TravelPlan


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

    assert any(item.item_id == "locked-1" for item in response.trip_plan.days[0].items)
    assert any("已锁定项目" in conflict for conflict in response.trip_plan.conflicts)


def test_no_map_result_does_not_create_placeholder_poi(monkeypatch):
    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], ["地图接口失败"]))
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")

    response = travel_agent.plan_travel("杭州附近有什么好吃的", [], search_enabled=False)

    assert response.poi_recommendations == []
    assert "地图接口失败" in response.trip_plan.risks
