import json
import sqlite3
import pytest

from agents.schemas import PlanDay, PlanItem, TransportOption, TravelPlan
from services.travel_store import PlanVersionConflict, TravelPlanStore


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
                        seat_count=6,
                    )
                ],
            ),
            PlanDay(date="2026-09-03", day_number=2, title="第 2 天"),
        ],
    )


def test_plan_versions_and_nested_items_survive_round_trip(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    plan = make_plan()
    plan.transport_options = [
        TransportOption(
            mode="flight",
            title="MU6549",
            provider="VariFlight",
            price="230",
            seat_count=10,
            source_ids=["transport_001_flight"],
        )
    ]
    first = store.save_plan_version(plan, user_id=7, change_summary="首次生成")
    first.days[0].items[0].detail = "不要自动替换"
    second = store.save_plan_version(first, user_id=7, change_summary="调整偏好")

    assert first.plan_id.startswith("plan_")
    assert first.version == 1
    assert second.version == 2
    assert second.previous_version == 1
    assert store.get_current_plan("guest_store_test", user_id=7).version == 2
    assert store.get_plan_version("guest_store_test", 1, user_id=7).days[0].items[0].locked is True
    assert store.get_plan_version("guest_store_test", 1, user_id=7).days[0].items[0].seat_count == 6
    assert store.get_plan_version("guest_store_test", 1, user_id=7).transport_options[0].seat_count == 10
    assert [item["version"] for item in store.list_versions("guest_store_test", user_id=7)] == [2, 1]


def test_store_does_not_return_another_users_plan(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    store.save_plan_version(make_plan("guest_isolated"), user_id=7)

    assert store.get_current_plan("guest_isolated", user_id=8) is None
    assert store.get_plan_version("guest_isolated", 1, user_id=8) is None
    assert store.get_current_plan("guest_isolated") is None


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


def test_conversation_turns_keep_travel_and_chat_insertion_order(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    store.save_turn(thread_id="mixed", role="user", content="先规划旅行")
    store.save_turn(thread_id="mixed", role="assistant", content="旅行计划", plan=make_plan("mixed"))
    store.save_turn(thread_id="mixed", role="user", turn_type="chat", content="再帮我写邮件")
    store.save_turn(thread_id="mixed", role="assistant", turn_type="chat", content="邮件草稿")

    turns = store.list_turns("mixed", turn_type=None)
    assert [(turn["turn_type"], turn["role"]) for turn in turns] == [
        ("travel", "user"),
        ("travel", "assistant"),
        ("chat", "user"),
        ("chat", "assistant"),
    ]


def test_legacy_snapshot_missing_optional_fields_gets_dataclass_defaults(tmp_path):
    db_path = tmp_path / "legacy.db"
    store = TravelPlanStore(db_path)
    payload = {
        "plan_id": "plan_legacy",
        "thread_id": "legacy_thread",
        "version": 1,
        "timezone": "Asia/Shanghai",
        "start_date": "2026-09-02",
        "end_date": "2026-09-02",
        "days": [{"date": "2026-09-02", "items": [{"title": "旧地点"}]}],
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO travel_plans(plan_id, thread_id, current_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("plan_legacy", "legacy_thread", 1, "", ""),
        )
        connection.execute(
            "INSERT INTO travel_plan_versions(plan_id, version, thread_id, payload, change_summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("plan_legacy", 1, "legacy_thread", json.dumps(payload), "", ""),
        )

    loaded = store.get_current_plan("legacy_thread")
    assert loaded is not None
    assert loaded.preferences == []
    assert loaded.days[0].title == ""
    assert loaded.days[0].items[0].status == "suggested"
    assert loaded.days[0].items[0].locked is False
    assert loaded.days[0].items[0].source_ids == []
    assert loaded.days[0].items[0].end_date == ""


def test_plan_write_rejects_stale_expected_version(tmp_path):
    store = TravelPlanStore(tmp_path / "travel.db")
    first = store.save_plan_version(make_plan("cas"))
    store.save_plan_version(first, expected_version=first.version)

    with pytest.raises(PlanVersionConflict):
        store.save_plan_version(first, expected_version=first.version)

    assert store.get_current_plan("cas").version == 2


def test_turn_only_thread_enforces_stored_user_ownership(tmp_path):
    store = TravelPlanStore(tmp_path / "turn_only.db")
    store.save_turn(thread_id="turn_only", role="user", content="用户 7 的聊天", user_id=7)

    assert store.list_turns("turn_only", user_id=7)[0]["content"] == "用户 7 的聊天"
    with pytest.raises(PermissionError):
        store.list_turns("turn_only", user_id=8)
    with pytest.raises(PermissionError):
        store.list_turns("turn_only")
    with pytest.raises(PermissionError):
        store.save_turn(thread_id="turn_only", role="assistant", content="越权写入", user_id=8)


def test_mixed_plan_and_turn_owners_fail_closed(tmp_path):
    store = TravelPlanStore(tmp_path / "mixed_owner.db")
    store.save_plan_version(make_plan("mixed_owner"), user_id=7)

    with sqlite3.connect(tmp_path / "mixed_owner.db") as connection:
        connection.execute(
            "INSERT INTO travel_turns(thread_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            ("mixed_owner", 8, "user", "不一致的历史归属", ""),
        )

    with pytest.raises(PermissionError):
        store.list_turns("mixed_owner", user_id=7)
    with pytest.raises(PermissionError):
        store.list_turns("mixed_owner")


def test_guest_capability_is_required_for_existing_guest_turns(tmp_path):
    store = TravelPlanStore(tmp_path / "guest_access.db")
    token = store.ensure_guest_access("guest_bound")
    assert token
    store.save_turn(thread_id="guest_bound", role="user", content="匿名旅行请求")

    assert store.ensure_guest_access("guest_bound") is None
    assert store.ensure_guest_access("guest_bound", "wrong-token") is None
    assert store.ensure_guest_access("guest_bound", token) == token


def test_guest_access_can_verify_existing_binding_without_minting_one(tmp_path):
    store = TravelPlanStore(tmp_path / "legacy_guest_access.db")
    token = store.ensure_guest_access("guest_legacy", allow_new_binding=True)
    assert token

    # A legacy checkpoint is checked by the API layer before this call.  The
    # storage seam must still support verifying an already-bound capability
    # while refusing to create a new one.
    assert store.ensure_guest_access("guest_legacy", token, allow_new_binding=False) == token
    assert store.ensure_guest_access("guest_unbound_legacy", allow_new_binding=False) is None


def test_guest_access_expires_on_server_even_if_token_matches(tmp_path):
    store = TravelPlanStore(tmp_path / "guest_ttl.db")
    token = store.ensure_guest_access("guest_ttl")
    assert token

    with sqlite3.connect(tmp_path / "guest_ttl.db") as connection:
        connection.execute(
            "UPDATE travel_guest_access SET created_at = ?, last_seen_at = ? WHERE thread_id = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", "guest_ttl"),
        )

    refreshed = store.ensure_guest_access("guest_ttl", token)
    assert refreshed and refreshed != token

    durable_store = TravelPlanStore(tmp_path / "guest_ttl_durable.db")
    durable_token = durable_store.ensure_guest_access("guest_ttl_durable")
    durable_store.save_turn(
        thread_id="guest_ttl_durable",
        role="user",
        content="过期前已经产生的数据",
    )
    with sqlite3.connect(tmp_path / "guest_ttl_durable.db") as connection:
        connection.execute(
            "UPDATE travel_guest_access SET created_at = ?, last_seen_at = ? WHERE thread_id = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", "guest_ttl_durable"),
        )
    assert durable_store.ensure_guest_access("guest_ttl_durable", durable_token) is None


def test_out_of_range_items_survive_plan_round_trip(tmp_path):
    store = TravelPlanStore(tmp_path / "out_of_range.db")
    plan = make_plan("out_of_range")
    plan.out_of_range_items = [
        PlanItem(
            "locked-outside",
            "hotel",
            "日期外酒店",
            "2026-09-04",
            status="confirmed",
            locked=True,
        )
    ]

    saved = store.save_plan_version(plan)
    loaded = store.get_current_plan("out_of_range")

    assert saved.out_of_range_items[0].item_id == "locked-outside"
    assert loaded.out_of_range_items[0].date == "2026-09-04"
