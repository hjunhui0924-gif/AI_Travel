from concurrent.futures import ThreadPoolExecutor
from time import sleep

from agents.schemas import Evidence, PlaceCandidate, TransportPage, TransportOption, TransportQueryPage, TravelPlan, TravelQuery
from adapters.variflight_adapter import VariFlightResults
import pytest
from services import flight_service, rail_service, route_service, transport_service


@pytest.fixture(autouse=True)
def _reset_route_cache():
    route_service.clear_route_cache()
    yield
    route_service.clear_route_cache()


def _query() -> TravelQuery:
    return TravelQuery(
        raw_text="上海到杭州",
        intent="trip_plan",
        origin="上海",
        destination="杭州",
        city="杭州",
        date="2026-09-02",
        start_date="2026-09-02",
        end_date="2026-09-02",
    )


def test_one_malformed_flight_row_does_not_discard_valid_rows(monkeypatch):
    monkeypatch.setattr(
        flight_service,
        "search_flights",
        lambda *args: [
            {"flight_no": "GOOD", "depart_time": "10:00", "arrive_time": "11:00", "provider": "test-flight"},
            {"flight_no": "BAD", "depart_time": "12:00", "arrive_time": "13:00", "provider": "test-flight"},
        ],
    )

    def resolve_transport_dates(travel_date, item):
        if item["flight_no"] == "BAD":
            raise ValueError("malformed provider date")
        return travel_date, travel_date

    monkeypatch.setattr(flight_service, "resolve_transport_dates", resolve_transport_dates)

    results = flight_service.get_flight_options(_query())

    assert [item.title for item in results] == ["GOOD"]
    assert results.partial is True
    assert results.errors == ["row:dict"]


def test_invalid_flight_schema_row_is_not_exposed_as_transport_option(monkeypatch):
    monkeypatch.setattr(
        flight_service,
        "search_flights",
        lambda *args: [
            {"flight_no": "GOOD", "depart_time": "10:00", "arrive_time": "11:00", "provider": "test-flight"},
            {"flight_no": "Flight", "depart_time": "", "arrive_time": "11:00", "provider": "test-flight"},
        ],
    )

    results = flight_service.get_flight_options(_query())

    assert [item.title for item in results] == ["GOOD"]
    assert results.errors == ["malformed provider row"]


def test_invalid_rail_schema_row_is_not_exposed_as_transport_option(monkeypatch):
    monkeypatch.setattr(
        rail_service,
        "query_left_tickets",
        lambda *args: [
            {"train_no": "G123", "depart_time": "08:00", "arrive_time": "09:00"},
            {"train_no": "车次", "depart_time": "", "arrive_time": "09:00"},
        ],
    )

    results = rail_service.get_rail_options(_query())

    assert [item.title for item in results] == ["G123"]
    assert results.errors == ["malformed provider row"]


def test_flight_option_preserves_provider_seat_count(monkeypatch):
    monkeypatch.setattr(
        flight_service,
        "search_flights",
        lambda *args: [
            {
                "flight_no": "MU6549",
                "depart_time": "23:30",
                "arrive_time": "00:40",
                "depart_date": "2026-09-02",
                "arrive_date": "2026-09-03",
                "price": "230",
                "seat_count": 10,
                "cabin": "经济舱",
                "provider": "VariFlight",
            }
        ],
    )

    results = flight_service.get_flight_options(_query())

    assert len(results) == 1
    assert results[0].seat_count == 10


def test_partial_variflight_rows_reach_flight_result_diagnostics(monkeypatch):
    monkeypatch.setattr(
        flight_service,
        "search_flights",
        lambda *args: VariFlightResults(
            [
                {
                    "flight_no": "MU6549",
                    "depart_time": "10:00",
                    "arrive_time": "11:00",
                    "price": "230",
                    "provider": "VariFlight",
                }
            ],
            errors=["provider_rows_schema_changed:1"],
        ),
    )

    results = flight_service.get_flight_options(_query())

    assert len(results) == 1
    assert results.partial is True
    assert results.errors == ["provider_rows_schema_changed:1"]


def test_route_mode_failure_does_not_block_other_modes(monkeypatch):
    monkeypatch.setattr(
        route_service,
        "_build_segments",
        lambda query: [
            (
                {"name": "西湖", "formatted_address": "西湖", "location": "120,30"},
                {"name": "灵隐寺", "formatted_address": "灵隐寺", "location": "120,31"},
            )
        ],
    )

    def fake_plan_route(origin, destination, *, strategy):
        if strategy == "transit":
            raise TimeoutError("transit unavailable")
        return {
            "origin": origin,
            "destination": destination,
            "duration": "20 分钟" if strategy == "driving" else "40 分钟",
            "distance": "2 公里",
            "summary": strategy,
        }

    monkeypatch.setattr(route_service, "plan_route", fake_plan_route)

    results = route_service.get_route_plans(_query())

    assert len(results) == 1
    assert results[0].mode == "driving"
    assert results.partial is True
    assert results.errors == ["transit:TimeoutError"]


def test_route_service_keeps_geometry_for_map_preview(monkeypatch):
    monkeypatch.setattr(
        route_service,
        "_build_segments",
        lambda query: [
            (
                {"name": "西湖", "formatted_address": "西湖", "location": "120,30"},
                {"name": "灵隐寺", "formatted_address": "灵隐寺", "location": "120.1,30.1"},
            )
        ],
    )

    def fake_plan_route(origin, destination, *, strategy):
        if strategy == "driving":
            return {
                "origin": origin,
                "destination": destination,
                "duration": "20 分钟",
                "distance": "2 公里",
                "summary": "驾车",
                "origin_location": "120,30",
                "destination_location": "120.1,30.1",
                "polyline": [[120.0, 30.0], [120.1, 30.1]],
            }
        return None

    monkeypatch.setattr(route_service, "plan_route", fake_plan_route)

    results = route_service.get_route_plans(_query())

    assert len(results) == 1
    assert results[0].origin_location == "120,30"
    assert results[0].destination_location == "120.1,30.1"
    assert results[0].polyline == [[120.0, 30.0], [120.1, 30.1]]


def test_route_service_builds_bounded_directed_matrix(monkeypatch):
    places = [
        {"name": "A", "formatted_address": "A", "location": "120,30"},
        {"name": "B", "formatted_address": "B", "location": "120.1,30.1"},
        {"name": "C", "formatted_address": "C", "location": "120.2,30.2"},
    ]
    monkeypatch.setattr(
        route_service,
        "_build_segments",
        lambda query, **kwargs: [
            (origin, destination)
            for origin in places
            for destination in places
            if origin is not destination
        ],
    )
    calls: list[tuple[str, str, str]] = []

    def fake_plan_route(origin, destination, *, strategy):
        calls.append((origin, destination, strategy))
        return {
            "origin": origin,
            "destination": destination,
            "duration": "10 分钟",
            "duration_minutes": 10,
            "distance": "1000 米",
            "distance_meters": 1000,
            "summary": strategy,
            "polyline": [[120.0, 30.0], [120.1, 30.1]],
        }

    monkeypatch.setattr(route_service, "plan_route", fake_plan_route)
    query = _query()
    query.named_places = ["A", "B", "C"]
    result = route_service.get_route_plans(query, complete_matrix=True)

    assert result.complete is True
    assert result.attempted_requests == 18
    assert len(result) == 6
    assert len(calls) == 18
    assert {(item.origin, item.destination) for item in result} == {
        ("A", "B"), ("A", "C"), ("B", "A"),
        ("B", "C"), ("C", "A"), ("C", "B"),
    }


def test_route_service_uses_confirmed_place_identity_without_regeocoding(monkeypatch):
    query = _query()
    query.named_places = ["万象城", "西湖"]
    query.resolved_place_candidates = [
        PlaceCandidate(
            candidate_id="candidate-wx",
            provider_id="amap-wx",
            name="万象城",
            city="杭州",
            address="已确认地址",
            location="120.10,30.20",
            query_text="万象城",
        ),
        PlaceCandidate(
            candidate_id="candidate-xh",
            provider_id="amap-xh",
            name="西湖",
            city="杭州",
            address="已确认地址2",
            location="120.11,30.21",
            query_text="西湖",
        ),
    ]
    monkeypatch.setattr(
        route_service,
        "resolve_place_in_city",
        lambda *args: (_ for _ in ()).throw(AssertionError("confirmed place must not be re-geocoded")),
    )
    monkeypatch.setattr(
        route_service,
        "plan_route",
        lambda origin, destination, *, strategy: {
            "duration": "10 分钟",
            "distance": "1000 米",
            "duration_minutes": 10,
            "distance_meters": 1000,
            "origin_location": origin,
            "destination_location": destination,
            "polyline": [[120.10, 30.20], [120.11, 30.21]],
        },
    )

    result = route_service.get_route_plans(query, max_requests=1)

    assert result[0].origin_place_id == "amap-wx"
    assert result[0].destination_place_id == "amap-xh"
    assert result[0].origin_location == "120.10,30.20"


def test_route_service_budget_prioritizes_adjacent_chain_and_exposes_missing_edge(monkeypatch):
    places = [
        {"name": "A", "location": "120,30"},
        {"name": "B", "location": "120.1,30.1"},
        {"name": "C", "location": "120.2,30.2"},
    ]
    monkeypatch.setattr(
        route_service,
        "_build_segments",
        lambda query, **kwargs: [(places[0], places[1]), (places[1], places[2]), (places[0], places[2])],
    )
    monkeypatch.setenv("ROUTE_MATRIX_MODES", "transit,driving")
    monkeypatch.setattr(
        route_service,
        "plan_route",
        lambda origin, destination, *, strategy: {
            "duration": "10 分钟",
            "distance": "1000 米",
            "duration_minutes": 10,
            "distance_meters": 1000,
        },
    )

    query = _query()
    query.named_places = ["A", "B", "C"]
    result = route_service.get_route_plans(query, complete_matrix=True, max_requests=2)

    assert [(item.origin, item.destination) for item in result] == [("A", "B"), ("B", "C")]
    assert result.missing_edges == [("A", "C")]
    assert result.missing_edge_reasons[("A", "C")] == "budget_exhausted"
    assert "route_matrix_budget:2/6" in result.errors


def test_route_service_coalesces_same_cache_key_across_concurrent_calls(monkeypatch):
    places = [
        {"name": "A", "location": "121,31"},
        {"name": "B", "location": "121.1,31.1"},
    ]
    monkeypatch.setattr(route_service, "_build_segments", lambda query: [(places[0], places[1])])
    calls = 0

    def fake_plan_route(origin, destination, *, strategy):
        nonlocal calls
        calls += 1
        sleep(0.04)
        return {"duration": "10 分钟", "distance": "1000 米", "duration_minutes": 10, "distance_meters": 1000}

    monkeypatch.setattr(route_service, "plan_route", fake_plan_route)
    query = _query()
    query.named_places = ["A", "B"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: route_service.get_route_plans(query), range(2)))

    assert calls == 3
    assert first or second
    assert first.cache_hits + first.coalesced_requests + second.cache_hits + second.coalesced_requests >= 1


def test_high_speed_query_filters_regular_trains_and_has_no_fake_price(monkeypatch):
    monkeypatch.setattr(
        rail_service,
        "query_left_tickets",
        lambda *args: [
            {"train_no": "K8351", "depart_time": "04:45", "arrive_time": "06:31"},
            {"train_no": "G7541", "depart_time": "05:52", "arrive_time": "06:51"},
            {"train_no": "D61", "depart_time": "04:32", "arrive_time": "06:19"},
            {"train_no": "Z175", "depart_time": "04:08", "arrive_time": "05:50"},
        ],
    )
    query = TravelQuery(
        raw_text="2026-09-02 从上海到杭州查高铁",
        intent="rail_query",
        travel_mode="rail",
        origin="上海",
        destination="杭州",
        city="杭州",
        date="2026-09-02",
        start_date="2026-09-02",
        end_date="2026-09-02",
    )

    results = rail_service.get_rail_options(query)

    assert [item.title for item in results] == ["D61", "G7541"]
    assert all(item.price == "" for item in results)


def test_transport_page_returns_requested_slice_and_source_metadata(monkeypatch):
    options = [
        TransportOption(mode="rail", title=f"G10{i}", depart_date="2026-09-02", arrive_date="2026-09-02")
        for i in range(8)
    ]
    monkeypatch.setattr(
        transport_service,
        "get_rail_options",
        lambda query, *, offset, limit: rail_service.RailOptionsResult(
            options[offset : offset + limit],
            offset=offset,
            limit=limit,
            total_count=len(options),
        ),
    )
    plan = TravelPlan(
        plan_id="plan_transport_page",
        thread_id="guest_transport_page",
        version=1,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        origin="上海",
        destination="杭州",
        transport_pages=[
            TransportPage(mode="rail", offset=0, limit=5, returned_count=5, total_count=8, has_more=True, filter="high_speed")
        ],
    )

    response = transport_service.get_transport_page(plan, "rail", offset=5, limit=3)

    assert [item.title for item in response.options] == ["G105", "G106", "G107"]
    assert response.pages[0].returned_count == 3
    assert response.pages[0].total_count == 8
    assert response.pages[0].has_more is False
    assert response.pages[0].filter == "high_speed"
    assert response.options[0].source_ids == ["transport_rail_006"]
    assert response.sources[0].source_type == "rail_realtime"
