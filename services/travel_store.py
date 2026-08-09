"""Durable storage for travel plans, plan versions, and travel turns.

The chat checkpoint is optimized for LangGraph execution state.  A travel plan
has a different lifecycle: it must be addressable by date, version, and plan
item even when the assistant is not running.  This module keeps that public
domain state in a small SQLite store and deliberately stores only rendered
conversation text, never private model reasoning.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from agents.schemas import (
    Evidence,
    PlanDay,
    PlanItem,
    TransportOption,
    TravelConstraint,
    TravelFact,
    TravelPlan,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "resources" / "travel_plans.db"
GUEST_ACCESS_TTL_DAYS = 30


class PlanVersionConflict(RuntimeError):
    """Raised when a write is based on a stale plan version."""

    def __init__(self, expected_version: int | None, current_version: int | None):
        self.expected_version = expected_version
        self.current_version = current_version
        expected = "none" if expected_version is None else str(expected_version)
        current = "missing" if current_version is None else str(current_version)
        super().__init__(f"旅行计划版本已变化：expected={expected}, current={current}")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_guest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, TravelPlan):
        return asdict(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("travel plan must be a TravelPlan or mapping")


def _field_values(cls: type, raw: object, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    values = {key: source[key] for key in cls.__dataclass_fields__ if key in source}
    for key, default in (defaults or {}).items():
        if key not in values or values[key] is None:
            values[key] = default
    return values


def _list_value(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _legacy_item_id(raw_item: dict[str, Any], day: str) -> str:
    encoded = _json({"day": day, "item": raw_item})
    return f"legacy_{hashlib.sha1(encoded.encode('utf-8', errors='ignore')).hexdigest()[:12]}"


def _rebuild_plan_item(raw_item: object, day: str) -> PlanItem | None:
    if not isinstance(raw_item, dict):
        return None
    item_values = _field_values(
        PlanItem,
        raw_item,
        {
            "item_id": _legacy_item_id(raw_item, day),
            "item_type": "activity",
            "title": "未命名安排",
            "date": day,
            "start_time": "",
            "end_time": "",
            "location": "",
            "address": "",
            "detail": "",
            "status": "suggested",
            "locked": False,
            "source_ids": [],
            "estimated_cost": "",
            "travel_minutes": None,
            "confidence": "unknown",
            "end_date": "",
            "is_demo": False,
            "seat_count": None,
        },
    )
    item_values["source_ids"] = _list_value(item_values.get("source_ids"))
    return PlanItem(**item_values)


def _inclusive_day_count(start: object, end: object, fallback: int = 1) -> int:
    try:
        start_date = date.fromisoformat(str(start))
        end_date = date.fromisoformat(str(end))
    except (TypeError, ValueError):
        return max(1, fallback)
    return max(1, (end_date - start_date).days + 1)


def _rebuild_plan(payload: dict[str, Any]) -> TravelPlan:
    """Rebuild a typed plan from stored JSON, tolerating older snapshots."""

    days: list[PlanDay] = []
    for raw_day in payload.get("days") or []:
        if not isinstance(raw_day, dict):
            continue
        day_date = str(raw_day.get("date") or "")
        items = []
        for raw_item in raw_day.get("items") or []:
            item = _rebuild_plan_item(raw_item, day_date)
            if item is not None:
                items.append(item)
        day_values = _field_values(
            PlanDay,
            raw_day,
            {
                "date": day_date,
                "day_number": 0,
                "title": "",
                "summary": "",
                "has_conflicts": False,
            },
        )
        day_values["items"] = items
        days.append(PlanDay(**day_values))

    out_of_range_items = []
    for raw_item in payload.get("out_of_range_items") or []:
        day = str(raw_item.get("date") or "") if isinstance(raw_item, dict) else ""
        item = _rebuild_plan_item(raw_item, day)
        if item is not None:
            out_of_range_items.append(item)

    facts = []
    for raw_fact in payload.get("facts") or []:
        if isinstance(raw_fact, dict):
            facts.append(
                TravelFact(
                    **_field_values(
                        TravelFact,
                        raw_fact,
                        {
                            "fact_id": "",
                            "fact_type": "unknown",
                            "label": "",
                            "value": "",
                            "confirmed": False,
                            "source_ids": [],
                            "notes": "",
                        },
                    )
                )
            )
            facts[-1].source_ids = _list_value(facts[-1].source_ids)

    constraints = []
    for raw_constraint in payload.get("constraints") or []:
        if isinstance(raw_constraint, dict):
            constraints.append(
                TravelConstraint(
                    **_field_values(
                        TravelConstraint,
                        raw_constraint,
                        {
                            "constraint_id": "",
                            "kind": "preference",
                            "label": "",
                            "value": "",
                            "hard": False,
                            "satisfied": None,
                            "source": "user",
                        },
                    )
                )
            )

    transport_options = []
    for raw_option in payload.get("transport_options") or []:
        if isinstance(raw_option, dict):
            option_values = _field_values(
                TransportOption,
                raw_option,
                {
                    "mode": "flight",
                    "title": "",
                    "depart_time": "",
                    "arrive_time": "",
                    "duration": "",
                    "price": "",
                    "summary": "",
                    "provider": "",
                    "seats": [],
                    "is_demo": False,
                    "depart_date": "",
                    "arrive_date": "",
                    "seat_count": None,
                    "source_ids": [],
                },
            )
            option_values["seats"] = _list_value(option_values.get("seats"))
            option_values["source_ids"] = _list_value(option_values.get("source_ids"))
            transport_options.append(TransportOption(**option_values))

    sources = []
    for raw_source in payload.get("sources") or []:
        if isinstance(raw_source, dict):
            sources.append(
                Evidence(
                    **_field_values(
                        Evidence,
                        raw_source,
                        {
                            "evidence_id": "",
                            "source_type": "unknown",
                            "provider": "unknown",
                            "title": "",
                            "url": "",
                            "snippet": "",
                            "retrieved_at": "",
                            "valid_until": "",
                            "freshness": "unknown",
                            "reliability": "unknown",
                            "supports": [],
                            "is_demo": False,
                        },
                    )
                )
            )
            sources[-1].supports = _list_value(sources[-1].supports)

    first_day = days[0].date if days else ""
    last_day = days[-1].date if days else first_day
    values = _field_values(
        TravelPlan,
        payload,
        {
            "plan_id": "",
            "thread_id": "",
            "version": 1,
            "timezone": "Asia/Shanghai",
            "start_date": first_day,
            "end_date": last_day,
            "requested_days": _inclusive_day_count(
                payload.get("start_date") or first_day,
                payload.get("end_date") or last_day,
                len(days),
            ),
            "projected_days": len(days),
            "calendar_truncated": _inclusive_day_count(
                payload.get("start_date") or first_day,
                payload.get("end_date") or last_day,
                len(days),
            ) > len(days),
            "projection_end_date": last_day,
            "origin": "",
            "destination": "",
            "travelers": 1,
            "preferences": [],
            "summary": "",
            "out_of_range_items": [],
            "transport_options": [],
            "conflicts": [],
            "risks": [],
            "alerts": [],
            "diagnostics": [],
            "adapter_status": {},
            "search_enabled": False,
            "status": "draft",
            "created_at": "",
            "updated_at": "",
            "previous_version": None,
        },
    )
    for key in ("preferences", "conflicts", "risks", "alerts", "diagnostics"):
        values[key] = _list_value(values.get(key))
    values["adapter_status"] = (
        dict(values["adapter_status"]) if isinstance(values.get("adapter_status"), dict) else {}
    )
    values["days"] = days
    values["out_of_range_items"] = out_of_range_items
    values["facts"] = facts
    values["constraints"] = constraints
    values["sources"] = sources
    values["transport_options"] = transport_options
    # A malformed/old row should not make a thread impossible to open.
    return TravelPlan(**values)


class TravelPlanStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS travel_plans (
                    plan_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER,
                    current_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS travel_plan_versions (
                    plan_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    thread_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    change_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (plan_id, version),
                    FOREIGN KEY (plan_id) REFERENCES travel_plans(plan_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_travel_plan_versions_thread
                    ON travel_plan_versions(thread_id, version DESC);

                CREATE TABLE IF NOT EXISTS travel_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    user_id INTEGER,
                    turn_type TEXT NOT NULL DEFAULT 'travel',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments TEXT NOT NULL DEFAULT '[]',
                    search_enabled INTEGER NOT NULL DEFAULT 0,
                    plan_id TEXT,
                    plan_version INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_travel_turns_thread
                    ON travel_turns(thread_id, id);

                CREATE TABLE IF NOT EXISTS travel_guest_access (
                    thread_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                """
            )
            turn_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(travel_turns)").fetchall()
            }
            if "turn_type" not in turn_columns:
                connection.execute(
                    "ALTER TABLE travel_turns ADD COLUMN turn_type TEXT NOT NULL DEFAULT 'travel'"
                )

    def save_plan_version(
        self,
        plan: TravelPlan,
        *,
        user_id: int | None = None,
        change_summary: str = "",
        expected_version: int | None = None,
    ) -> TravelPlan:
        payload = _as_dict(plan)
        thread_id = str(payload.get("thread_id") or "").strip()
        if not thread_id:
            raise ValueError("travel plan thread_id is required")

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM travel_plans WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if current is not None:
                current_version = int(current["current_version"])
                if expected_version is not None and int(expected_version) != current_version:
                    raise PlanVersionConflict(expected_version, current_version)
                stored_user_id = current["user_id"]
                if (stored_user_id is None) != (user_id is None) or (
                    stored_user_id is not None
                    and user_id is not None
                    and int(stored_user_id) != int(user_id)
                ):
                    raise PermissionError("travel plan does not belong to this user")
                plan_id = str(current["plan_id"])
                version = current_version + 1
                created_at = str(current["created_at"])
                previous_version = int(current["current_version"])
                owner_id = stored_user_id if stored_user_id is not None else user_id
            else:
                # A first write participates in the CAS contract when the
                # caller passes 0.  ``None`` remains accepted for direct
                # legacy store callers; normal API/agent paths must pass 0
                # explicitly so concurrent first requests cannot overwrite
                # one another.
                if expected_version not in (None, 0):
                    raise PlanVersionConflict(expected_version, None)
                plan_id = str(payload.get("plan_id") or f"plan_{uuid4().hex}")
                version = 1
                created_at = str(payload.get("created_at") or _now_utc())
                previous_version = None
                owner_id = user_id

            now = _now_utc()
            payload.update(
                {
                    "plan_id": plan_id,
                    "thread_id": thread_id,
                    "version": version,
                    "created_at": created_at,
                    "updated_at": now,
                    "previous_version": previous_version,
                }
            )
            encoded = _json(payload)
            if current is None:
                connection.execute(
                    """
                    INSERT INTO travel_plans
                        (plan_id, thread_id, user_id, current_version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (plan_id, thread_id, owner_id, version, created_at, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE travel_plans
                    SET user_id = COALESCE(user_id, ?), current_version = ?, updated_at = ?
                    WHERE plan_id = ?
                    """,
                    (owner_id, version, now, plan_id),
                )
            connection.execute(
                """
                INSERT INTO travel_plan_versions
                    (plan_id, version, thread_id, payload, change_summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (plan_id, version, thread_id, encoded, change_summary or "", now),
            )

        return _rebuild_plan(payload)

    def get_current_plan(self, thread_id: str, user_id: int | None = None) -> TravelPlan | None:
        row = self._get_plan_row(thread_id, user_id=user_id)
        if row is None:
            return None
        return self.get_plan_version(thread_id, int(row["current_version"]), user_id=user_id)

    def list_versions(self, thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
        owner = self._get_plan_row(thread_id, user_id=user_id)
        if owner is None:
            return []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT version, change_summary, created_at
                FROM travel_plan_versions
                WHERE thread_id = ?
                ORDER BY version DESC
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "plan_id": str(owner["plan_id"]),
                "version": int(row["version"]),
                "change_summary": str(row["change_summary"] or ""),
                "created_at": str(row["created_at"] or ""),
            }
            for row in rows
        ]

    def get_plan_version(
        self,
        thread_id: str,
        version: int,
        user_id: int | None = None,
    ) -> TravelPlan | None:
        owner = self._get_plan_row(thread_id, user_id=user_id)
        if owner is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload
                FROM travel_plan_versions
                WHERE plan_id = ? AND thread_id = ? AND version = ?
                """,
                (str(owner["plan_id"]), thread_id, int(version)),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload"]))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        payload["thread_id"] = payload.get("thread_id") or thread_id
        payload["plan_id"] = payload.get("plan_id") or str(owner["plan_id"])
        return _rebuild_plan(payload)

    def save_turn(
        self,
        *,
        thread_id: str,
        role: str,
        turn_type: str = "travel",
        content: str,
        user_id: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        search_enabled: bool = False,
        plan: TravelPlan | None = None,
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("travel turn role must be user or assistant")
        if turn_type not in {"travel", "chat"}:
            raise ValueError("turn_type must be travel or chat")
        self._assert_owner(thread_id, user_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO travel_turns
                    (thread_id, user_id, turn_type, role, content, attachments, search_enabled,
                     plan_id, plan_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    user_id,
                    turn_type,
                    role,
                    content,
                    _json(attachments or []),
                    int(bool(search_enabled)),
                    plan.plan_id if plan else None,
                    plan.version if plan else None,
                    _now_utc(),
                ),
            )

    def list_turns(
        self,
        thread_id: str,
        user_id: int | None = None,
        *,
        turn_type: str | None = "travel",
    ) -> list[dict[str, Any]]:
        self._assert_owner(thread_id, user_id)
        with self._lock, self._connect() as connection:
            query = """
                SELECT id, turn_type, role, content, attachments, search_enabled,
                       plan_id, plan_version, created_at
                FROM travel_turns
                WHERE thread_id = ?
            """
            params: list[Any] = [thread_id]
            if turn_type is not None:
                query += " AND turn_type = ?"
                params.append(turn_type)
            query += " ORDER BY id ASC"
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            try:
                attachments = json.loads(str(row["attachments"] or "[]"))
            except json.JSONDecodeError:
                attachments = []
            result.append(
                {
                    "id": int(row["id"]),
                    "turn_type": str(row["turn_type"] or "travel"),
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "attachments": attachments if isinstance(attachments, list) else [],
                    "search_enabled": bool(row["search_enabled"]),
                    "plan_id": row["plan_id"],
                    "plan_version": row["plan_version"],
                    "created_at": str(row["created_at"] or ""),
                }
            )
        return result

    def delete_thread(self, thread_id: str, user_id: int | None = None) -> None:
        self._assert_owner(thread_id, user_id)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM travel_turns WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM travel_guest_access WHERE thread_id = ?", (thread_id,))
            row = connection.execute(
                "SELECT plan_id FROM travel_plans WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if row is not None:
                connection.execute("DELETE FROM travel_plans WHERE thread_id = ?", (thread_id,))

    def _get_plan_row(self, thread_id: str, user_id: int | None = None) -> sqlite3.Row | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM travel_plans WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if row is None:
            return None
        if user_id is None and row["user_id"] is not None:
            return None
        if user_id is not None and row["user_id"] is not None and int(row["user_id"]) != int(user_id):
            return None
        return row

    def get_plan_owner_id(self, thread_id: str) -> int | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM travel_plans WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if row is None or row["user_id"] is None:
            return None
        return int(row["user_id"])

    def _assert_owner(self, thread_id: str, user_id: int | None) -> None:
        with self._lock, self._connect() as connection:
            plan_row = connection.execute(
                "SELECT user_id FROM travel_plans WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            owner_rows = ([plan_row] if plan_row is not None else []) + connection.execute(
                """
                SELECT user_id FROM travel_turns WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchall()
        if not owner_rows:
            return

        stored_owners = {
            None if row["user_id"] is None else int(row["user_id"])
            for row in owner_rows
        }
        requested_owner = None if user_id is None else int(user_id)
        if stored_owners != {requested_owner}:
            raise PermissionError("travel turns do not belong to this user")

    def ensure_guest_access(
        self,
        thread_id: str,
        presented_token: str = "",
        *,
        allow_new_binding: bool = True,
    ) -> str | None:
        """Bind an anonymous guest thread to a random HttpOnly capability.

        A new capability can be created only for a thread with no durable
        plan/turn data.  Existing legacy guest data without a binding is
        intentionally not opened anonymously because the server cannot prove
        who originally owned it.
        """

        presented_token = (presented_token or "").strip()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, created_at, last_seen_at FROM travel_guest_access WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if row is not None:
                now = datetime.now(timezone.utc)
                try:
                    created_at = datetime.fromisoformat(str(row["created_at"]))
                    last_seen_at = datetime.fromisoformat(str(row["last_seen_at"]))
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    if last_seen_at.tzinfo is None:
                        last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    return None
                expiry = now - timedelta(days=GUEST_ACCESS_TTL_DAYS)
                if created_at < expiry or last_seen_at < expiry:
                    # Let an otherwise empty guest thread start a fresh
                    # capability, while durable plan/turn data below still
                    # prevents a silent re-bind after expiry.
                    connection.execute(
                        "DELETE FROM travel_guest_access WHERE thread_id = ?",
                        (thread_id,),
                    )
                    presented_token = ""
                    row = None
                else:
                    if not presented_token or not hmac.compare_digest(
                        str(row["token_hash"]), _hash_guest_token(presented_token)
                    ):
                        return None
                    connection.execute(
                        "UPDATE travel_guest_access SET last_seen_at = ? WHERE thread_id = ?",
                        (_now_utc(), thread_id),
                    )
                    return presented_token

            if not allow_new_binding:
                return None

            existing_data = connection.execute(
                """
                SELECT 1 FROM travel_plans WHERE thread_id = ?
                UNION ALL
                SELECT 1 FROM travel_turns WHERE thread_id = ?
                LIMIT 1
                """,
                (thread_id, thread_id),
            ).fetchone()
            if existing_data is not None:
                return None

            token = presented_token or secrets.token_urlsafe(32)
            now = _now_utc()
            connection.execute(
                """
                INSERT INTO travel_guest_access(thread_id, token_hash, created_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, _hash_guest_token(token), now, now),
            )
            return token


travel_plan_store = TravelPlanStore()


def save_plan_version(
    plan: TravelPlan,
    *,
    user_id: int | None = None,
    change_summary: str = "",
    expected_version: int | None = None,
) -> TravelPlan:
    return travel_plan_store.save_plan_version(
        plan,
        user_id=user_id,
        change_summary=change_summary,
        expected_version=expected_version,
    )


def get_current_plan(thread_id: str, user_id: int | None = None) -> TravelPlan | None:
    return travel_plan_store.get_current_plan(thread_id, user_id=user_id)


def get_plan_owner_id(thread_id: str) -> int | None:
    return travel_plan_store.get_plan_owner_id(thread_id)


def list_plan_versions(thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
    return travel_plan_store.list_versions(thread_id, user_id=user_id)


def get_plan_version(thread_id: str, version: int, user_id: int | None = None) -> TravelPlan | None:
    return travel_plan_store.get_plan_version(thread_id, version, user_id=user_id)


def save_travel_turn(**kwargs: Any) -> None:
    travel_plan_store.save_turn(**kwargs)


def list_travel_turns(thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
    return travel_plan_store.list_turns(thread_id, user_id=user_id, turn_type="travel")


def save_conversation_turn(**kwargs: Any) -> None:
    kwargs["turn_type"] = "chat"
    travel_plan_store.save_turn(**kwargs)


def list_conversation_turns(thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
    return travel_plan_store.list_turns(thread_id, user_id=user_id, turn_type=None)


def ensure_guest_access(
    thread_id: str,
    presented_token: str = "",
    *,
    allow_new_binding: bool = True,
) -> str | None:
    return travel_plan_store.ensure_guest_access(
        thread_id,
        presented_token,
        allow_new_binding=allow_new_binding,
    )


def delete_travel_thread(thread_id: str, user_id: int | None = None) -> None:
    travel_plan_store.delete_thread(thread_id, user_id=user_id)


def serialize_plan(plan: TravelPlan | None) -> dict[str, Any] | None:
    return asdict(plan) if plan is not None else None
