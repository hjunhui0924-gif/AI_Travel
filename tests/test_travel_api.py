from uuid import uuid4
import json

from fastapi.testclient import TestClient

import app as app_module
from agents import agent as agent_module
from agents.schemas import PlanDay, PlanItem, TravelPlan, TravelPlanResponse
from services.travel_store import delete_travel_thread


def test_guest_travel_plan_and_calendar_endpoints():
    thread_id = f"guest_{uuid4().hex}"
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
    finally:
        delete_travel_thread(thread_id)


def test_chat_done_contains_structured_plan_and_version(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"

    def fake_plan(message, attachments, *, thread_id="", search_enabled=False, current_plan=None, activity_logger=None):
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
    client = TestClient(app_module.app)
    try:
        first = client.post(
            "/chat",
            data={"message": "旅行", "thread_id": thread_id, "search_enabled": "false"},
        )
        done = json.loads([line[6:] for line in first.text.splitlines() if line.startswith("data:")][-1])
        assert done["trip_plan"]["version"] == 1

        second = client.post(
            "/chat",
            data={"message": "重新规划", "thread_id": thread_id, "search_enabled": "false"},
        )
        done_again = json.loads([line[6:] for line in second.text.splitlines() if line.startswith("data:")][-1])
        assert done_again["trip_plan"]["version"] == 2
    finally:
        delete_travel_thread(thread_id)
