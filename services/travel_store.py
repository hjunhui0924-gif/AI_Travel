"""Durable storage for travel plans, plan versions, and travel turns.

The chat checkpoint is optimized for LangGraph execution state.  A travel plan
has a different lifecycle: it must be addressable by date, version, and plan
item even when the assistant is not running.  This module keeps that public
domain state in a small SQLite store and deliberately stores only rendered
conversation text, never private model reasoning.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from agents.schemas import (
    Evidence,
    PlanDay,
    PlanItem,
    TravelConstraint,
    TravelFact,
    TravelPlan,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "resources" / "travel_plans.db"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _rebuild_plan(payload: dict[str, Any]) -> TravelPlan:
    """Rebuild a typed plan from stored JSON, tolerating older snapshots."""

    days: list[PlanDay] = []
    for raw_day in payload.get("days") or []:
        if not isinstance(raw_day, dict):
            continue
        items = []
        for raw_item in raw_day.get("items") or []:
            if isinstance(raw_item, dict):
                items.append(PlanItem(**{key: raw_item.get(key) for key in PlanItem.__dataclass_fields__}))
        day_values = {key: raw_day.get(key) for key in PlanDay.__dataclass_fields__ if key != "items"}
        day_values["items"] = items
        days.append(PlanDay(**day_values))

    facts = []
    for raw_fact in payload.get("facts") or []:
        if isinstance(raw_fact, dict):
            facts.append(TravelFact(**{key: raw_fact.get(key) for key in TravelFact.__dataclass_fields__}))

    constraints = []
    for raw_constraint in payload.get("constraints") or []:
        if isinstance(raw_constraint, dict):
            constraints.append(
                TravelConstraint(
                    **{key: raw_constraint.get(key) for key in TravelConstraint.__dataclass_fields__}
                )
            )

    sources = []
    for raw_source in payload.get("sources") or []:
        if isinstance(raw_source, dict):
            sources.append(Evidence(**{key: raw_source.get(key) for key in Evidence.__dataclass_fields__}))

    values = {
        key: payload.get(key)
        for key in TravelPlan.__dataclass_fields__
        if key not in {"days", "facts", "constraints", "sources"}
    }
    values["days"] = days
    values["facts"] = facts
    values["constraints"] = constraints
    values["sources"] = sources
    # A malformed/old row should not make a thread impossible to open.
    values.setdefault("timezone", "Asia/Shanghai")
    values.setdefault("version", 1)
    values.setdefault("travelers", 1)
    values.setdefault("preferences", [])
    values.setdefault("conflicts", [])
    values.setdefault("risks", [])
    values.setdefault("search_enabled", False)
    values.setdefault("status", "draft")
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
                """
            )

    def save_plan_version(
        self,
        plan: TravelPlan,
        *,
        user_id: int | None = None,
        change_summary: str = "",
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
                stored_user_id = current["user_id"]
                if stored_user_id is not None and user_id is not None and int(stored_user_id) != int(user_id):
                    raise PermissionError("travel plan does not belong to this user")
                plan_id = str(current["plan_id"])
                version = int(current["current_version"]) + 1
                created_at = str(current["created_at"])
                previous_version = int(current["current_version"])
                owner_id = stored_user_id if stored_user_id is not None else user_id
            else:
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
        return _rebuild_plan(payload)

    def save_turn(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        user_id: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        search_enabled: bool = False,
        plan: TravelPlan | None = None,
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("travel turn role must be user or assistant")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO travel_turns
                    (thread_id, user_id, role, content, attachments, search_enabled,
                     plan_id, plan_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    user_id,
                    role,
                    content,
                    _json(attachments or []),
                    int(bool(search_enabled)),
                    plan.plan_id if plan else None,
                    plan.version if plan else None,
                    _now_utc(),
                ),
            )

    def list_turns(self, thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
        self._assert_owner(thread_id, user_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, attachments, search_enabled, plan_id, plan_version, created_at
                FROM travel_turns
                WHERE thread_id = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()
        result = []
        for row in rows:
            try:
                attachments = json.loads(str(row["attachments"] or "[]"))
            except json.JSONDecodeError:
                attachments = []
            result.append(
                {
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

    def delete_thread(self, thread_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM travel_turns WHERE thread_id = ?", (thread_id,))
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
        if user_id is not None and row["user_id"] is not None and int(row["user_id"]) != int(user_id):
            return None
        return row

    def _assert_owner(self, thread_id: str, user_id: int | None) -> None:
        if user_id is None:
            return
        row = self._get_plan_row(thread_id)
        if row is not None and row["user_id"] is not None and int(row["user_id"]) != int(user_id):
            raise PermissionError("travel turns do not belong to this user")
        # A thread may not have a plan yet; callers still need to be able to
        # ask for an empty history.  Ownership of the chat thread is checked
        # by the HTTP layer, not guessed from this optional store.


travel_plan_store = TravelPlanStore()


def save_plan_version(plan: TravelPlan, *, user_id: int | None = None, change_summary: str = "") -> TravelPlan:
    return travel_plan_store.save_plan_version(plan, user_id=user_id, change_summary=change_summary)


def get_current_plan(thread_id: str, user_id: int | None = None) -> TravelPlan | None:
    return travel_plan_store.get_current_plan(thread_id, user_id=user_id)


def list_plan_versions(thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
    return travel_plan_store.list_versions(thread_id, user_id=user_id)


def get_plan_version(thread_id: str, version: int, user_id: int | None = None) -> TravelPlan | None:
    return travel_plan_store.get_plan_version(thread_id, version, user_id=user_id)


def save_travel_turn(**kwargs: Any) -> None:
    travel_plan_store.save_turn(**kwargs)


def list_travel_turns(thread_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
    return travel_plan_store.list_turns(thread_id, user_id=user_id)


def delete_travel_thread(thread_id: str) -> None:
    travel_plan_store.delete_thread(thread_id)


def serialize_plan(plan: TravelPlan | None) -> dict[str, Any] | None:
    return asdict(plan) if plan is not None else None
