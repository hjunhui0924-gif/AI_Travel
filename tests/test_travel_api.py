from uuid import uuid4
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain.messages import AIMessage, AIMessageChunk, HumanMessage
from starlette.requests import Request

import app as app_module
from agents import agent as agent_module
from agents.schemas import (
    Evidence,
    PlanDay,
    PlanItem,
    RoutePlan,
    TransportPage,
    TransportOption,
    TransportQueryPage,
    TravelPlan,
    TravelPlanResponse,
)
from services.travel_store import (
    delete_travel_thread,
    ensure_guest_access,
    save_travel_turn,
    travel_plan_store,
)


def test_guest_travel_plan_and_calendar_endpoints():
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-03",
        destination="杭州",
        days=[
            PlanDay(
                date="2026-09-02",
                day_number=1,
                title="第 1 天",
                summary="西湖",
                items=[PlanItem("poi-1", "attraction", "西湖", "2026-09-02")],
            ),
            PlanDay(date="2026-09-03", day_number=2, title="第 2 天", summary="灵隐寺"),
        ],
    )
    saved = app_module.save_plan_version(plan)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)
    try:
        response = client.get(f"/travel/plans/{thread_id}")
        assert response.status_code == 200
        assert response.json()["plan"]["plan_id"] == saved.plan_id

        calendar = client.get(f"/travel/plans/{thread_id}/calendar")
        assert calendar.status_code == 200
        assert [item["date"] for item in calendar.json()["days"]] == ["2026-09-02", "2026-09-03"]

        day = client.get(f"/travel/plans/{thread_id}/days/2026-09-03")
        assert day.status_code == 200
        assert day.json()["day"]["summary"] == "灵隐寺"

        updated = client.patch(f"/travel/plans/{thread_id}/items/poi-1", json={"locked": True})
        assert updated.status_code == 200
        assert updated.json()["item"]["locked"] is True
        assert updated.json()["item"]["status"] == "confirmed"
        assert updated.json()["plan"]["version"] == 2

        stale = client.patch(
            f"/travel/plans/{thread_id}/items/poi-1",
            json={"locked": False, "expected_version": 1},
        )
        assert stale.status_code == 409
    finally:
        delete_travel_thread(thread_id)


def test_guest_route_map_endpoint_keeps_key_server_side(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
        route_plans=[
            RoutePlan(
                mode="walking",
                origin="西湖",
                destination="灵隐寺",
                origin_location="120.121358,30.222692",
                destination_location="120.101406,30.240826",
                polyline=[[120.121358, 30.222692], [120.101406, 30.240826]],
            )
        ],
    )
    app_module.save_plan_version(plan)
    monkeypatch.setattr(
        app_module,
        "fetch_static_route_map",
        lambda routes: (b"fake-png", "image/png"),
    )
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.get(f"/travel/plans/{thread_id}/map")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == b"fake-png"
        assert "AMAP_WEB_API_KEY" not in response.text
    finally:
        delete_travel_thread(thread_id)


def test_guest_route_map_endpoint_falls_back_to_svg(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
        route_plans=[
            RoutePlan(
                mode="driving",
                origin="西湖",
                destination="灵隐寺",
                polyline=[[120.0, 30.0], [120.1, 30.1]],
            )
        ],
    )
    app_module.save_plan_version(plan)
    monkeypatch.setattr(app_module, "fetch_static_route_map", lambda routes: None)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.get(f"/travel/plans/{thread_id}/map")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.headers["x-route-map-fallback"] == "true"
        assert "路线示意图" in response.text
    finally:
        delete_travel_thread(thread_id)


def test_chat_done_contains_structured_plan_and_version(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    expected_versions = []

    def fake_plan(
        message,
        attachments,
        *,
        thread_id="",
        search_enabled=False,
        current_plan=None,
        activity_logger=None,
        supervisor_result=None,
    ):
        plan = TravelPlan(
            plan_id="",
            thread_id=thread_id,
            version=0,
            timezone="Asia/Shanghai",
            start_date="2026-09-02",
            end_date="2026-09-02",
            destination="杭州",
            days=[
                PlanDay(
                    date="2026-09-02",
                    day_number=1,
                    items=[PlanItem("item-1", "attraction", "西湖", "2026-09-02")],
                )
            ],
        )
        return TravelPlanResponse(intent="trip_plan", summary="ok", trip_plan=plan, sources=plan.sources)

    monkeypatch.setattr(agent_module, "plan_travel", fake_plan)
    original_save_plan_version = agent_module.save_plan_version

    def spy_save_plan_version(*args, **kwargs):
        expected_versions.append(kwargs.get("expected_version"))
        return original_save_plan_version(*args, **kwargs)

    monkeypatch.setattr(agent_module, "save_plan_version", spy_save_plan_version)
    client = TestClient(app_module.app)
    try:
        first = client.post(
            "/chat",
            data={"message": "旅行", "thread_id": thread_id, "search_enabled": "false"},
        )
        done = json.loads([line[6:] for line in first.text.splitlines() if line.startswith("data:")][-1])
        assert done["trip_plan"]["version"] == 1
        assert expected_versions == [0]

        second = client.post(
            "/chat",
            data={"message": "重新规划", "thread_id": thread_id, "search_enabled": "false"},
        )
        done_again = json.loads([line[6:] for line in second.text.splitlines() if line.startswith("data:")][-1])
        assert done_again["trip_plan"]["version"] == 2
        assert expected_versions == [0, 1]
    finally:
        delete_travel_thread(thread_id)


def test_replan_uses_supervisor_before_composing_new_plan(monkeypatch):
    from agents.travel_supervisor import TravelSupervisorResult

    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    current = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
    )
    app_module.save_plan_version(current, expected_version=0)
    captured = {}

    def fake_supervisor(model, message, attachments, query, **kwargs):
        captured["query"] = query
        return TravelSupervisorResult(query=query, decision="plan")

    def fake_plan(message, attachments, **kwargs):
        captured["supervisor_result"] = kwargs.get("supervisor_result")
        plan = TravelPlan(
            plan_id="",
            thread_id=thread_id,
            version=0,
            timezone="Asia/Shanghai",
            start_date="2026-09-02",
            end_date="2026-09-02",
            destination="杭州",
            days=[PlanDay(date="2026-09-02", day_number=1)],
            transport_options=[TransportOption(mode="rail", title="G123", provider="12306", depart_time="08:00", arrive_time="09:00")],
        )
        return TravelPlanResponse(intent="trip_replan", summary="已完成调整。", trip_plan=plan)

    monkeypatch.setattr(app_module, "run_travel_supervisor", fake_supervisor)
    monkeypatch.setattr(app_module, "plan_travel", fake_plan)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.post(
            f"/travel/plans/{thread_id}/replan",
            json={"message": "调整为轻松节奏", "search_enabled": False},
        )

        assert response.status_code == 200
        assert captured["query"].destination == "杭州"
        assert captured["supervisor_result"].decision == "plan"
    finally:
        delete_travel_thread(thread_id)


def test_replan_hides_internal_provider_errors(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    current = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
    )
    app_module.save_plan_version(current, expected_version=0)

    def failing_supervisor(*args, **kwargs):
        raise RuntimeError("provider-secret-token /private/provider/path")

    monkeypatch.setattr(app_module, "run_travel_supervisor", failing_supervisor)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.post(
            f"/travel/plans/{thread_id}/replan",
            json={"message": "调整为轻松节奏", "search_enabled": False},
        )

        assert response.status_code == 500
        assert app_module.INTERNAL_SSE_ERROR_MESSAGE in response.text
        assert "provider-secret-token" not in response.text
        assert "/private/provider/path" not in response.text
    finally:
        delete_travel_thread(thread_id)


def test_transport_pagination_endpoint_returns_structured_page(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        origin="上海",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
        transport_options=[
            TransportOption(mode="rail", title=f"G10{i}") for i in range(5)
        ],
        transport_pages=[
            TransportPage(mode="rail", returned_count=5, total_count=8, has_more=True, filter="high_speed")
        ],
    )
    app_module.save_plan_version(plan)
    page = TransportPage(
        mode="rail",
        offset=5,
        limit=3,
        returned_count=3,
        total_count=8,
        has_more=False,
        filter="high_speed",
    )
    option = TransportOption(mode="rail", title="G105", source_ids=["transport_rail_006"])
    source = Evidence(
        evidence_id="transport_rail_006",
        source_type="rail_realtime",
        provider="12306",
        title="G105",
        reliability="adapter_result",
    )
    monkeypatch.setattr(
        app_module,
        "get_transport_page",
        lambda *args, **kwargs: TransportQueryPage(options=[option], pages=[page], sources=[source]),
    )
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.get(
            f"/travel/plans/{thread_id}/transport?mode=rail&offset=5&limit=3"
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["options"][0]["title"] == "G105"
        assert payload["page"]["has_more"] is False
        assert payload["page"]["filter"] == "high_speed"
        assert payload["sources"][0]["provider"] == "12306"
    finally:
        delete_travel_thread(thread_id)


def test_chat_clarification_for_province_trip_is_structured_and_persisted():
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.post(
            "/chat",
            data={"message": "江苏五日游", "thread_id": thread_id, "search_enabled": "false"},
        )
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        done = events[-1]
        assert done["trip_plan"] is None
        assert done["clarification"]["code"] == "destination_cities"
        assert len(done["clarification"]["options"]) >= 3

        history = client.get(f"/history/{thread_id}")
        assert history.status_code == 200
        assistant = history.json()["messages"][-1]
        assert assistant["clarification"]["code"] == "destination_cities"
    finally:
        delete_travel_thread(thread_id)


def test_chat_clarification_follow_up_merges_original_requirement(monkeypatch):
    from agents import travel_agent
    from agents import travel_supervisor
    from agents.travel_supervisor import TravelSupervisorResult

    monkeypatch.setattr(travel_agent, "recommend_pois", lambda query, **kwargs: ([], [], [], []))
    monkeypatch.setattr(travel_agent, "get_route_plans", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_rail_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_flight_options", lambda query: [])
    monkeypatch.setattr(travel_agent, "get_weather_summary", lambda location, forecast=False: "")
    monkeypatch.setattr(travel_supervisor, "recommend_pois", lambda *args, **kwargs: ([], [], [], []))
    monkeypatch.setattr(travel_supervisor, "get_route_plans", lambda *args, **kwargs: [])
    monkeypatch.setattr(travel_supervisor, "get_rail_options", lambda *args, **kwargs: [])
    monkeypatch.setattr(travel_supervisor, "get_flight_options", lambda *args, **kwargs: [])
    monkeypatch.setattr(travel_supervisor, "get_weather_summary", lambda *args, **kwargs: "")
    captured_query = {}

    def fake_supervisor(model, message, attachments, query, **kwargs):
        captured_query.update(
            {
                "destination": query.destination,
                "destination_cities": list(query.destination_cities),
                "origin": query.origin,
            }
        )
        return TravelSupervisorResult(query=query, decision="plan")

    monkeypatch.setattr(agent_module, "run_travel_supervisor", fake_supervisor)

    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    def done_payload(response):
        return json.loads(
            [line[6:] for line in response.text.splitlines() if line.startswith("data:")][-1]
        )

    try:
        first = client.post(
            "/chat",
            data={"message": "江苏五日游", "thread_id": thread_id, "search_enabled": "false"},
        )
        assert done_payload(first)["clarification"]["code"] == "destination_cities"

        second = client.post(
            "/chat",
            data={
                "message": "选择 A，从上海出发",
                "thread_id": thread_id,
                "search_enabled": "false",
            },
        )
        done = done_payload(second)
        assert done["clarification"] is None
        assert done["trip_plan"] is None
        assert "暂不生成行程计划" in done["final_text"]
        assert captured_query == {
            "destination": "江苏",
            "destination_cities": ["南京", "扬州"],
            "origin": "上海",
        }
    finally:
        delete_travel_thread(thread_id)


def test_chat_rejects_non_travel_question_after_scope_classification(monkeypatch):
    class FakeModel:
        def invoke(self, messages):
            return AIMessage(content='{"is_travel_request": false}')

    monkeypatch.setattr(agent_module, "model", FakeModel())

    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.post(
            "/chat",
            data={"message": "帮我写一封邮件", "thread_id": thread_id, "search_enabled": "false"},
        )
        assert response.status_code == 200
        assert "只支持旅行相关" in response.text
        assert '"scope_refusal": true' in response.text
        assert '"trip_plan": null' in response.text
    finally:
        delete_travel_thread(thread_id)


def test_guest_id_cannot_read_owned_thread(monkeypatch):
    thread_id = "guest_abcd1234"
    monkeypatch.setattr(app_module, "get_user_by_session_token", lambda token: None)
    monkeypatch.setattr(app_module, "get_thread", lambda value: {"thread_id": value, "user_id": 7} if value == thread_id else None)
    client = TestClient(app_module.app)

    response = client.get(f"/travel/plans/{thread_id}")

    assert response.status_code == 404


def test_old_checkpoint_only_guest_thread_cannot_mint_new_capability(monkeypatch):
    thread_id = "guest_legacy_checkpoint"
    monkeypatch.setattr(app_module, "get_user_by_session_token", lambda token: None)
    monkeypatch.setattr(app_module, "get_thread", lambda value: None)
    monkeypatch.setattr(app_module, "get_plan_owner_id", lambda value: None)
    monkeypatch.setattr(app_module, "has_checkpoint_data", lambda value: value == thread_id)
    client = TestClient(app_module.app)

    response = client.get(f"/history/{thread_id}")

    assert response.status_code == 404


def test_old_checkpoint_cannot_be_claimed_with_arbitrary_guest_cookie(monkeypatch):
    thread_id = "guest_legacy_checkpoint_cookie"
    monkeypatch.setattr(app_module, "get_user_by_session_token", lambda token: None)
    monkeypatch.setattr(app_module, "get_thread", lambda value: None)
    monkeypatch.setattr(app_module, "get_plan_owner_id", lambda value: None)
    monkeypatch.setattr(app_module, "has_checkpoint_data", lambda value: value == thread_id)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, "attacker-controlled-token")

    response = client.get(f"/history/{thread_id}")

    assert response.status_code == 404


def test_legacy_thread_migration_is_explicit_and_only_claims_known_ids(monkeypatch):
    monkeypatch.setattr(app_module, "get_user_by_session_token", lambda token: {"id": 42})
    monkeypatch.setattr(app_module, "list_legacy_thread_ids", lambda: ["guest_legacy_known_1234"])
    claimed = []
    monkeypatch.setattr(
        app_module,
        "assign_threads_to_user",
        lambda user_id, thread_ids, title: claimed.extend(thread_ids),
    )
    monkeypatch.setattr(
        app_module,
        "is_thread_owned_by_user",
        lambda user_id, thread_id: thread_id in claimed,
    )
    client = TestClient(app_module.app)
    client.cookies.set(app_module.SESSION_COOKIE_NAME, "valid-session")

    response = client.post(
        "/threads/migrate-legacy",
        json={"thread_ids": ["guest_legacy_known_1234", "not-a-known-legacy-id", "default"]},
    )

    assert response.status_code == 200
    assert response.json()["migrated_thread_ids"] == ["guest_legacy_known_1234"]
    assert response.json()["skipped_thread_ids"] == ["not-a-known-legacy-id", "default"]
    assert claimed == ["guest_legacy_known_1234"]


def test_guest_request_mints_http_only_capability_cookie():
    thread_id = f"guest_{uuid4().hex}"
    client = TestClient(app_module.app)

    try:
        response = client.get(f"/travel/plans/{thread_id}")

        assert response.status_code == 200
        assert response.json()["plan"] is None
        token = client.cookies.get(app_module.GUEST_COOKIE_NAME)
        assert token
        set_cookie = response.headers.get("set-cookie", "")
        assert f"{app_module.GUEST_COOKIE_NAME}={token}" in set_cookie
        assert "HttpOnly" in set_cookie
    finally:
        delete_travel_thread(thread_id)


def test_guest_plan_requires_matching_capability_cookie():
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
    )
    app_module.save_plan_version(plan)

    try:
        no_cookie = TestClient(app_module.app).get(f"/travel/plans/{thread_id}")
        assert no_cookie.status_code == 404

        wrong_cookie_client = TestClient(app_module.app)
        wrong_cookie_client.cookies.set(app_module.GUEST_COOKIE_NAME, "wrong-token")
        wrong_cookie = wrong_cookie_client.get(f"/travel/plans/{thread_id}")
        assert wrong_cookie.status_code == 404

        authorized_client = TestClient(app_module.app)
        authorized_client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)
        authorized = authorized_client.get(f"/travel/plans/{thread_id}")
        assert authorized.status_code == 200
        assert authorized.json()["plan"]["destination"] == "杭州"
    finally:
        delete_travel_thread(thread_id)


def test_guest_login_claims_current_session_into_account(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    save_travel_turn(thread_id=thread_id, role="user", content="游客保留的请求")
    username = f"claim_{uuid4().hex[:12]}"
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    response = client.post(
        "/auth/register",
        data={
            "username": username,
            "password": "secret123",
            "display_name": "Claim User",
            "guest_thread_id": thread_id,
            "guest_title": "我的游客旅行",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["guest_claim"]["status"] == "claimed"
    assert body["claimed_thread"]["thread_id"] == thread_id
    user_id = int(body["user"]["id"])
    assert app_module.get_thread(thread_id)["user_id"] == user_id
    assert app_module.get_plan_owner_id(thread_id) is None
    assert travel_plan_store.list_turns(thread_id, user_id=user_id)[0]["content"] == "游客保留的请求"
    assert app_module.ensure_guest_access(thread_id, guest_token) is None

    history = client.get(f"/history/{thread_id}")
    assert history.status_code == 200
    assert history.json()["messages"][0]["content"] == "游客保留的请求"

    try:
        app_module.delete_thread_record(user_id, thread_id)
    finally:
        app_module.delete_thread(thread_id, user_id=user_id)


def test_claimed_guest_thread_can_continue_chat_as_authenticated_user(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    save_travel_turn(thread_id=thread_id, role="user", content="游客保留的请求")
    username = f"claim_chat_{uuid4().hex[:12]}"

    def fake_stream_chat(**_kwargs):
        yield AIMessageChunk(content=[{"type": "text", "text": "登录后继续聊天"}]), {}

    monkeypatch.setattr(app_module, "stream_chat", fake_stream_chat)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    response = client.post(
        "/auth/register",
        data={
            "username": username,
            "password": "secret123",
            "guest_thread_id": thread_id,
        },
    )
    assert response.status_code == 200
    user_id = int(response.json()["user"]["id"])

    try:
        chat_response = client.post(
            "/chat",
            data={"message": "继续安排", "thread_id": thread_id, "search_enabled": "false"},
        )
        assert chat_response.status_code == 200
        assert "登录后继续聊天" in chat_response.text
        assert "event: error" not in chat_response.text
    finally:
        app_module.delete_thread_record(user_id, thread_id)
        app_module.delete_thread(thread_id, user_id=user_id)


def test_chat_stream_hides_internal_exception_details(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)

    def failing_stream_chat(**_kwargs):
        raise RuntimeError("provider-secret-token and /private/provider/path")

    monkeypatch.setattr(app_module, "stream_chat", failing_stream_chat)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.post(
            "/chat",
            data={"message": "测试内部错误", "thread_id": thread_id, "search_enabled": "false"},
        )
        assert response.status_code == 200
        assert app_module.INTERNAL_SSE_ERROR_MESSAGE in response.text
        assert "provider-secret-token" not in response.text
        assert "/private/provider/path" not in response.text
    finally:
        delete_travel_thread(thread_id)


def test_authenticated_user_cannot_chat_on_unclaimed_guest_thread():
    guest_thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(guest_thread_id)
    username = f"deny_chat_{uuid4().hex[:12]}"
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)
    response = client.post(
        "/auth/register",
        data={"username": username, "password": "secret123"},
    )
    assert response.status_code == 200

    try:
        denied = client.post(
            "/chat",
            data={
                "message": "不应读到别人的游客会话",
                "thread_id": guest_thread_id,
                "search_enabled": "false",
            },
        )
        assert denied.status_code == 200
        assert "不能使用 guest 会话 ID" in denied.text
    finally:
        user_id = int(response.json()["user"]["id"])
        account_threads = app_module.list_threads_for_user(user_id)
        for thread in account_threads:
            app_module.delete_thread_record(user_id, thread["thread_id"])
            app_module.delete_thread(thread["thread_id"], user_id=user_id)
        delete_travel_thread(guest_thread_id)


def test_calendar_lazy_projection_export_and_share_endpoints():
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-03",
        origin="广州",
        destination="杭州",
        days=[
            PlanDay(
                date="2026-09-02",
                day_number=1,
                title="第一天",
                summary="西湖",
                items=[PlanItem("lazy-item", "attraction", "西湖", "2026-09-02")],
            ),
            PlanDay(date="2026-09-03", day_number=2, title="第二天"),
        ],
    )
    saved = app_module.save_plan_version(plan, expected_version=0)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        header = client.get(f"/travel/plans/{thread_id}?include_days=false")
        assert header.status_code == 200
        assert header.json()["plan"]["days"] == []

        calendar = client.get(f"/travel/plans/{thread_id}/calendar?version={saved.version}")
        assert [day["date"] for day in calendar.json()["days"]] == ["2026-09-02", "2026-09-03"]
        day = client.get(f"/travel/plans/{thread_id}/days/2026-09-02?version=1")
        assert day.json()["day"]["items"][0]["item_id"] == "lazy-item"

        markdown = client.get(f"/travel/plans/{thread_id}/export?format=markdown")
        assert markdown.status_code == 200
        assert "西湖" in markdown.text
        assert ".md" in markdown.headers["content-disposition"]
        exported_json = client.get(f"/travel/plans/{thread_id}/export?format=json")
        assert exported_json.status_code == 200
        assert exported_json.json()["version"] == 1

        created = client.post(f"/travel/plans/{thread_id}/shares", json={"expires_days": 5})
        assert created.status_code == 200
        share = created.json()["share"]
        public = TestClient(app_module.app).get(share["api_url"])
        assert public.status_code == 200
        assert public.json()["plan"]["thread_id"] == ""
        html = TestClient(app_module.app).get(share["url"])
        assert html.status_code == 200
        assert "西湖" in html.text

        revoked = client.delete(f"/travel/plans/{thread_id}/shares/{share['share_id']}")
        assert revoked.status_code == 200
        assert TestClient(app_module.app).get(share["api_url"]).status_code == 404
    finally:
        delete_travel_thread(thread_id)


def test_out_of_range_plan_item_can_be_unlocked_via_patch():
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)
    plan = TravelPlan(
        plan_id="",
        thread_id=thread_id,
        version=0,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        destination="杭州",
        days=[PlanDay(date="2026-09-02", day_number=1)],
        out_of_range_items=[
            PlanItem(
                "outside-item",
                "hotel",
                "日期外酒店",
                "2026-09-03",
                status="confirmed",
                locked=True,
            )
        ],
    )
    saved = app_module.save_plan_version(plan)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)

    try:
        response = client.patch(
            f"/travel/plans/{thread_id}/items/outside-item",
            json={"locked": False, "expected_version": saved.version},
        )

        assert response.status_code == 200
        assert response.json()["item"]["locked"] is False
        assert response.json()["plan"]["out_of_range_items"][0]["status"] == "suggested"
    finally:
        delete_travel_thread(thread_id)


def test_logged_in_request_cannot_use_guest_namespace(monkeypatch):
    monkeypatch.setattr(app_module, "get_user_by_session_token", lambda token: {"id": 7})
    request = Request({"type": "http", "headers": []})

    with pytest.raises(HTTPException) as raised:
        app_module._authorized_thread_user(request, "guest_abc123")

    assert raised.value.status_code == 400


def test_logged_in_user_can_open_explicitly_migrated_guest_thread(monkeypatch):
    monkeypatch.setattr(app_module, "get_user_by_session_token", lambda token: {"id": 7})
    monkeypatch.setattr(app_module, "is_thread_owned_by_user", lambda user_id, thread_id: True)
    request = Request({"type": "http", "headers": []})

    assert app_module._authorized_thread_user(request, "guest_migrated_old") == {"id": 7}


def test_mixed_travel_and_regular_history_keeps_turn_order(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)

    def fake_stream_chat(**kwargs):
        yield AIMessageChunk(content=[{"type": "text", "text": "普通聊天回复"}]), {}

    monkeypatch.setattr(app_module, "stream_chat", fake_stream_chat)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)
    save_travel_turn(thread_id=thread_id, role="user", content="旅行 T1")
    save_travel_turn(thread_id=thread_id, role="assistant", content="旅行回复 T1")
    try:
        response = client.post(
            "/chat",
            data={"message": "普通 G1", "thread_id": thread_id, "search_enabled": "false"},
        )
        assert response.status_code == 200

        save_travel_turn(thread_id=thread_id, role="user", content="旅行 T2")
        save_travel_turn(thread_id=thread_id, role="assistant", content="旅行回复 T2")
        history = client.get(f"/history/{thread_id}")
        assert history.status_code == 200
        assert [item["content"] for item in history.json()["messages"]] == [
            "旅行 T1",
            "旅行回复 T1",
            "普通 G1",
            "普通聊天回复",
            "旅行 T2",
            "旅行回复 T2",
        ]
    finally:
        delete_travel_thread(thread_id)


def test_legacy_checkpoint_and_travel_turns_are_merged_by_timestamp(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    old_user = HumanMessage(content="普通 T0")
    old_assistant = AIMessage(content="普通 A0")
    new_user = HumanMessage(content="普通 T2")
    new_assistant = AIMessage(content="普通 A2")
    snapshots = [
        SimpleNamespace(
            checkpoint={
                "ts": "2026-08-09T02:00:00+00:00",
                "channel_values": {
                    "messages": [old_user, old_assistant, new_user, new_assistant]
                },
            },
            metadata={},
        ),
        SimpleNamespace(
            checkpoint={
                "ts": "2026-08-09T00:00:00+00:00",
                "channel_values": {"messages": [old_user, old_assistant]},
            },
            metadata={},
        ),
    ]
    travel_turns = [
        {
            "turn_type": "travel",
            "role": "user",
            "content": "旅行 T1",
            "attachments": [],
            "search_enabled": False,
            "plan_id": None,
            "plan_version": None,
            "created_at": "2026-08-09T01:00:00+00:00",
        },
        {
            "turn_type": "travel",
            "role": "assistant",
            "content": "旅行 A1",
            "attachments": [],
            "search_enabled": False,
            "plan_id": "plan_1",
            "plan_version": 1,
            "created_at": "2026-08-09T01:01:00+00:00",
        },
        {
            "turn_type": "chat",
            "role": "user",
            "content": "普通 T3",
            "attachments": [],
            "search_enabled": False,
            "plan_id": None,
            "plan_version": None,
            "created_at": "2026-08-09T03:00:00+00:00",
        },
        {
            "turn_type": "chat",
            "role": "assistant",
            "content": "普通 A3",
            "attachments": [],
            "search_enabled": False,
            "plan_id": None,
            "plan_version": None,
            "created_at": "2026-08-09T03:01:00+00:00",
        },
    ]
    monkeypatch.setattr(agent_module.checkpoint, "list", lambda config: snapshots)
    monkeypatch.setattr(
        agent_module.checkpoint,
        "get",
        lambda config: {"channel_values": {"messages": [old_user, old_assistant, new_user, new_assistant]}},
    )
    monkeypatch.setattr(agent_module, "list_conversation_turns", lambda *args, **kwargs: travel_turns)
    monkeypatch.setattr(agent_module, "list_travel_turns", lambda *args, **kwargs: travel_turns)

    messages = agent_module.get_messages(thread_id)

    assert [item["content"] for item in messages] == [
        "普通 T0",
        "普通 A0",
        "旅行 T1",
        "旅行 A1",
        "普通 T2",
        "普通 A2",
        "普通 T3",
        "普通 A3",
    ]


def test_legacy_history_keeps_later_repeated_user_message(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    old_user = HumanMessage(content="同一句话")
    old_assistant = AIMessage(content="旧回复")
    snapshots = [
        SimpleNamespace(
            checkpoint={
                "ts": "2026-08-09T00:00:00+00:00",
                "channel_values": {"messages": [old_user, old_assistant]},
            },
            metadata={},
        )
    ]
    turns = [
        {
            "turn_type": "chat",
            "role": "user",
            "content": "同一句话",
            "attachments": [],
            "search_enabled": False,
            "plan_id": None,
            "plan_version": None,
            "created_at": "2026-08-09T01:00:00+00:00",
        },
        {
            "turn_type": "chat",
            "role": "assistant",
            "content": "新回复",
            "attachments": [],
            "search_enabled": False,
            "plan_id": None,
            "plan_version": None,
            "created_at": "2026-08-09T01:01:00+00:00",
        },
    ]
    monkeypatch.setattr(agent_module.checkpoint, "list", lambda config: snapshots)
    monkeypatch.setattr(
        agent_module.checkpoint,
        "get",
        lambda config: {"channel_values": {"messages": [old_user, old_assistant]}},
    )
    monkeypatch.setattr(agent_module, "list_conversation_turns", lambda *args, **kwargs: turns)

    messages = agent_module.get_messages(thread_id)

    assert [item["content"] for item in messages] == ["同一句话", "旧回复", "同一句话", "新回复"]
