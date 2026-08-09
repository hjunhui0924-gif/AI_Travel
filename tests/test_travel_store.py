from agents.schemas import PlanDay, PlanItem, TravelPlan
from services.travel_store import TravelPlanStore


def make_plan(thread_id: str = "guest_store_test") -> TravelPlan:
    return TravelPlan(
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
                items=[
                    PlanItem(
                        item_id="locked-hotel",
                        item_type="hotel",
                        title="已确认酒店",
                        date="2026-09-02",
                        status="confirmed",
                        locked=True,
                    )
                ],
            ),
            PlanDay(date="2026-09-03", day_number=2, title="第 2 天"),
        ],
    )


def test_plan_versions_and_nested_items_survive_round_trip(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    first = store.save_plan_version(make_plan(), user_id=7, change_summary="首次生成")
    first.days[0].items[0].detail = "不要自动替换"
    second = store.save_plan_version(first, user_id=7, change_summary="调整偏好")

    assert first.plan_id.startswith("plan_")
    assert first.version == 1
    assert second.version == 2
    assert second.previous_version == 1
    assert store.get_current_plan("guest_store_test", user_id=7).version == 2
    assert store.get_plan_version("guest_store_test", 1, user_id=7).days[0].items[0].locked is True
    assert [item["version"] for item in store.list_versions("guest_store_test", user_id=7)] == [2, 1]


def test_store_does_not_return_another_users_plan(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    store.save_plan_version(make_plan("guest_isolated"), user_id=7)

    assert store.get_current_plan("guest_isolated", user_id=8) is None
    assert store.get_plan_version("guest_isolated", 1, user_id=8) is None


def test_travel_turns_are_persisted_and_deleted(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    plan = store.save_plan_version(make_plan("guest_turns"))
    store.save_turn(thread_id="guest_turns", role="user", content="帮我安排杭州", plan=plan)
    store.save_turn(thread_id="guest_turns", role="assistant", content="已生成计划", plan=plan)

    turns = store.list_turns("guest_turns")
    assert [item["role"] for item in turns] == ["user", "assistant"]
    assert turns[-1]["plan_version"] == 1

    store.delete_thread("guest_turns")
    assert store.get_current_plan("guest_turns") is None
    assert store.list_turns("guest_turns") == []

