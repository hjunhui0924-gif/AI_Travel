from __future__ import annotations

import time

from langchain.messages import AIMessage

from agents.schemas import OpenSkyFlightStatus, PlaceCandidate, PoiRecommendation, RoutePlan, TravelPlan, TravelPlanResponse, TravelQuery
from agents import travel_agent
from services.itinerary_optimizer import optimize_itinerary
from services.provider_orchestrator import run_provider_orchestrator
from services.travel_finalizer import finalize_travel_response
from agents import travel_supervisor
from agents import agent as agent_runtime
from services.rail_service import RailOptionsResult
from services.flight_service import FlightOptionsResult
from services.poi_recommender import PoiRecommendationResult


def _query(**overrides) -> TravelQuery:
    values = {
        "raw_text": "杭州一日游",
        "intent": "trip_plan",
        "origin": "上海",
        "destination": "杭州",
        "city": "杭州",
        "start_date": "2026-10-01",
        "end_date": "2026-10-01",
        "date": "2026-10-01",
        "days": 1,
        "named_places": ["A", "B", "C"],
        "objective": "fastest",
    }
    values.update(overrides)
    return TravelQuery(**values)


def test_provider_orchestrator_runs_independent_tasks_in_parallel_and_retries_timeout():
    attempts = {"rail": 0}

    def rail():
        attempts["rail"] += 1
        if attempts["rail"] == 1:
            raise TimeoutError("temporary")
        return ["G123"]

    def slow(value):
        def run():
            time.sleep(0.08)
            return [value]

        return run

    started = time.monotonic()
    result = run_provider_orchestrator(
        {"rail": rail, "flight": slow("flight"), "weather": slow("weather"), "poi": slow("poi")}
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.6
    assert attempts["rail"] == 2
    assert result.result("rail").value == ["G123"]
    assert result.result("rail").meta.attempts == 2
    assert result.result("rail").meta.status == "success"
    assert result.result("flight").meta.status == "success"


def test_provider_orchestrator_does_not_retry_invalid_input():
    result = run_provider_orchestrator({"poi": lambda: (_ for _ in ()).throw(ValueError("bad args"))})

    task = result.result("poi")
    assert task.meta.attempts == 1
    assert task.meta.retryable is False
    assert task.meta.status == "failed"


def test_supervisor_executes_independent_transport_tools_in_parallel(monkeypatch):
    class BatchModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "search_rail", "args": {}, "id": "rail"},
                        {"name": "search_flight", "args": {}, "id": "flight"},
                    ],
                )
            return AIMessage(content='{"decision":"answer","answer":"已完成比较","reason":"用户要求比较"}')

    def rail(_query):
        time.sleep(0.08)
        return RailOptionsResult([])

    def flight(_query):
        time.sleep(0.08)
        return FlightOptionsResult([])

    monkeypatch.setattr(travel_supervisor, "get_rail_options", rail)
    monkeypatch.setattr(travel_supervisor, "get_flight_options", flight)
    query = _query(raw_text="比较高铁和航班", intent="transport_compare")
    started = time.monotonic()
    result = travel_supervisor.run_travel_supervisor(BatchModel(), query.raw_text, [], query)

    assert time.monotonic() - started < 0.25
    assert result.decision == "answer"
    assert result.adapter_status["rail"] == "empty"
    assert result.adapter_status["flight"] == "empty"


def test_place_candidate_id_follow_up_stays_in_travel_context():
    pending = {
        "place_candidates": [{"candidate_id": "place_123", "query_text": "西湖"}],
        "unresolved_places": ["西湖"],
    }

    assert agent_runtime._travel_route_kind("place_123", [], pending_query=pending) == "context"


def test_opensky_flight_query_returns_live_status_without_ticket_claims(monkeypatch):
    monkeypatch.setattr(travel_agent, "is_opensky_mode", lambda: True)
    monkeypatch.setattr(
        travel_agent,
        "get_flight_statuses",
        lambda: [
            OpenSkyFlightStatus(
                icao24="abc123",
                callsign="CA123",
                origin_country="China",
                latitude=39.9,
                longitude=116.4,
                on_ground=False,
            )
        ],
    )
    monkeypatch.setattr(
        travel_agent,
        "get_flight_options",
        lambda _query: (_ for _ in ()).throw(AssertionError("OpenSky mode must not request ticket offers")),
    )

    response = travel_agent.plan_travel("2026-09-02 从上海到杭州查航班", [], search_enabled=False)
    rendered = travel_agent.render_travel_response(response)

    assert response.trip_plan is None
    assert len(response.flight_statuses) == 1
    assert "不提供未来航班票价、余票" in rendered
    assert not any("未获取到可用航班结果" in alert for alert in response.alerts)


def test_itinerary_optimizer_returns_route_segments_for_fastest_order():
    places = [
        PoiRecommendation(name="A", category="景点", provider_id="a", source_ids=["a"]),
        PoiRecommendation(name="B", category="景点", provider_id="b", source_ids=["b"]),
        PoiRecommendation(name="C", category="景点", provider_id="c", source_ids=["c"]),
    ]
    routes = [
        RoutePlan(mode="driving", origin="A", destination="B", duration="50 分钟", distance="5000 米"),
        RoutePlan(mode="driving", origin="B", destination="C", duration="50 分钟", distance="5000 米"),
        RoutePlan(mode="driving", origin="A", destination="C", duration="10 分钟", distance="1000 米"),
        RoutePlan(mode="driving", origin="C", destination="B", duration="10 分钟", distance="1000 米"),
        RoutePlan(mode="driving", origin="B", destination="A", duration="10 分钟", distance="1000 米"),
        RoutePlan(mode="driving", origin="C", destination="A", duration="50 分钟", distance="5000 米"),
    ]

    result = optimize_itinerary(_query(), places, routes)

    assert [item.name for item in result.ordered_places] == ["A", "C", "B"]
    assert len(result.route_segments) == 2
    assert result.route_segments[0].buffer_minutes == 20
    assert result.score is not None


def test_ambiguous_place_blocks_route_lookup_until_confirmation(monkeypatch):
    candidate_a = PlaceCandidate(
        candidate_id="a",
        provider_id="a",
        name="万象城",
        city="深圳",
        district="罗湖区",
        address="宝安南路",
        query_text="万象城",
    )
    candidate_b = PlaceCandidate(
        candidate_id="b",
        provider_id="b",
        name="万象城",
        city="深圳",
        district="南山区",
        address="科苑南路",
        query_text="万象城",
    )
    query = _query(
        raw_text="深圳一日游，想去万象城",
        destination="深圳",
        city="深圳",
        named_places=["万象城"],
    )
    monkeypatch.setattr(
        "agents.travel_agent.recommend_pois",
        lambda *args, **kwargs: PoiRecommendationResult(
            recommendations=[],
            groups=[],
            evidence=[],
            errors=[],
            poi_status="success",
            place_candidates=[candidate_a, candidate_b],
            unresolved_places=["万象城"],
        ),
    )
    monkeypatch.setattr(
        "agents.travel_agent.get_route_plans",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("route must wait for confirmation")),
    )
    monkeypatch.setattr("agents.travel_agent.get_rail_options", lambda query: [])
    monkeypatch.setattr("agents.travel_agent.get_flight_options", lambda query: [])
    monkeypatch.setattr("agents.travel_agent.get_weather_summary", lambda *args, **kwargs: "")

    response = __import__("agents.travel_agent", fromlist=["plan_travel"]).plan_travel(
        query.raw_text,
        [],
        search_enabled=False,
    )

    assert response.clarification is not None
    assert response.clarification.code == "place_ambiguous"


def test_itinerary_optimizer_rejects_closed_place_window():
    places = [
        PoiRecommendation(
            name="A",
            category="景点",
            provider_id="a",
            source_ids=["a"],
            opening_windows=[],
        ),
        PoiRecommendation(
            name="B",
            category="景点",
            provider_id="b",
            source_ids=["b"],
        ),
    ]
    # A query whose first place can never be entered in the available window.
    places[0].opening_windows = [
        __import__("agents.schemas", fromlist=["OpeningWindow"]).OpeningWindow(
            date="2026-10-01", open_time="18:00", close_time="18:30", last_entry_time="18:00"
        )
    ]
    routes = [
        RoutePlan(mode="driving", origin="A", destination="B", duration="10 分钟"),
        RoutePlan(mode="driving", origin="B", destination="A", duration="10 分钟"),
    ]

    result = optimize_itinerary(_query(), places, routes)

    assert result.ordered_places
    assert any(item.code == "no_feasible_order" for item in result.diagnostics)
    assert result.conflicts


def test_finalizer_falls_back_when_model_introduces_unknown_date(monkeypatch):
    monkeypatch.setenv("TRAVEL_FINALIZER_ENABLED", "true")
    plan = TravelPlan(
        plan_id="p1",
        thread_id="t1",
        version=1,
        timezone="Asia/Shanghai",
        start_date="2026-10-01",
        end_date="2026-10-01",
        destination="杭州",
    )
    response = TravelPlanResponse(intent="trip_plan", summary="ok", trip_plan=plan, decision="plan")

    class FakeModel:
        def invoke(self, messages):
            return AIMessage(content="安排在 2027-01-01 出发。")

    assert finalize_travel_response(FakeModel(), "杭州一日游", response, lambda _: "确定性回退") == "确定性回退"
