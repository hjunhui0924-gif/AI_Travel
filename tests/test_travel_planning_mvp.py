from __future__ import annotations

import time

from langchain.messages import AIMessage

from agents.schemas import PlaceCandidate, PoiRecommendation, RoutePlan, TravelPlan, TravelPlanResponse, TravelQuery
from services.itinerary_optimizer import optimize_itinerary
from services.provider_orchestrator import run_provider_orchestrator
from services.travel_finalizer import finalize_travel_response
from agents import travel_supervisor
from agents import agent as agent_runtime
from services.rail_service import RailOptionsResult
from services.flight_service import FlightOptionsResult
from services.poi_recommender import PoiRecommendationResult
from services.route_service import RoutePlansResult


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


def test_itinerary_optimizer_keeps_explicit_order_with_adjacent_route_data():
    places = [
        PoiRecommendation(name="A", category="景点", provider_id="a", source_ids=["a"]),
        PoiRecommendation(name="B", category="景点", provider_id="b", source_ids=["b"]),
        PoiRecommendation(name="C", category="景点", provider_id="c", source_ids=["c"]),
    ]
    routes = [
        RoutePlan(mode="driving", origin="A", destination="B", duration="10 分钟", distance="1000 米"),
        RoutePlan(mode="driving", origin="B", destination="C", duration="20 分钟", distance="2000 米"),
    ]

    result = optimize_itinerary(_query(named_places=["A", "B", "C"]), places, routes)

    assert [item.name for item in result.ordered_places] == ["A", "B", "C"]
    assert [(item.origin_place_id, item.destination_place_id) for item in result.route_segments] == [
        ("a", "b"),
        ("b", "c"),
    ]


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
            date="2026-10-01",
            open_time="09:00",
            close_time="18:30",
            last_entry_time="18:00",
            closed_reason="closed",
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


def test_itinerary_optimizer_does_not_parse_route_summary_as_cost():
    places = [
        PoiRecommendation(name="A", category="景点", provider_id="a", source_ids=["a"]),
        PoiRecommendation(name="B", category="景点", provider_id="b", source_ids=["b"]),
    ]
    result = optimize_itinerary(
        _query(named_places=["A", "B"]),
        places,
        [
            RoutePlan(
                mode="transit",
                origin="A",
                destination="B",
                duration="35 分钟",
                distance="800 米",
                summary="公交约35分钟，步行800米",
                duration_minutes=35,
                distance_meters=800,
                estimated_cost=None,
            )
        ],
    )

    assert result.route_segments[0].estimated_cost is None


def test_itinerary_optimizer_selects_known_cost_alternative_without_name_keys():
    places = [
        PoiRecommendation(name="同名地点", category="景点", provider_id="place-a", source_ids=["a"]),
        PoiRecommendation(name="同名地点", category="景点", provider_id="place-b", source_ids=["b"]),
    ]
    result = optimize_itinerary(
        _query(named_places=[], objective="cheapest"),
        places,
        RoutePlansResult(
            [
                RoutePlan(
                    mode="driving",
                    origin="同名地点",
                    destination="同名地点",
                    duration="10 分钟",
                    origin_place_id="place-a",
                    destination_place_id="place-b",
                )
            ],
            alternatives={
                ("place-a", "place-b"): [
                    RoutePlan(
                        mode="driving",
                        origin="同名地点",
                        destination="同名地点",
                        duration="10 分钟",
                        origin_place_id="place-a",
                        destination_place_id="place-b",
                    ),
                    RoutePlan(
                        mode="transit",
                        origin="同名地点",
                        destination="同名地点",
                        duration="30 分钟",
                        estimated_cost=2.0,
                        cost_currency="CNY",
                        origin_place_id="place-a",
                        destination_place_id="place-b",
                    ),
                ]
            },
        ),
    )

    assert result.route_segments[0].mode == "transit"
    assert result.route_segments[0].estimated_cost == "2.0"


def test_itinerary_optimizer_does_not_silently_drop_explicit_places():
    names = ["A", "B", "C", "D", "E", "F"]
    places = [
        PoiRecommendation(name=name, category="景点", provider_id=name.lower(), source_ids=[name.lower()])
        for name in names
    ]

    result = optimize_itinerary(_query(named_places=names), places, [])

    assert [item.name for item in result.ordered_places] == names
    assert any(item.code == "no_feasible_order" for item in result.diagnostics)


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
