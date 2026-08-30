from agents.schemas import TravelQuery
from adapters.variflight_adapter import VariFlightResults
from services import flight_service, route_service


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
            {"flight_no": "GOOD", "depart_time": "10:00", "arrive_time": "11:00"},
            {"flight_no": "BAD", "depart_time": "12:00", "arrive_time": "13:00"},
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
