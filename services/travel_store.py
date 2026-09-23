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
    BookingRequirement,
    CareReminder,
    DailyWeather,
    Evidence,
    OpeningWindow,
    OptimizationDiagnostic,
    PlanDay,
    PlanItem,
    PlaceCandidate,
    ProviderMeta,
    RoutePlan,
    RouteSegment,
    ScheduleConstraint,
    TransportEdge,
    TransportPage,
    TransportOption,
    TravelConstraint,
    TravelFact,
    TravelPlan,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "resources" / "travel_plans.db"
GUEST_ACCESS_TTL_DAYS = 7
SHARE_DEFAULT_TTL_DAYS = 30
SHARE_MAX_TTL_DAYS = 90


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


def _parse_guest_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _guest_access_is_expired(
    created_at: object,
    last_seen_at: object,
    now: datetime,
) -> bool:
    """Treat malformed timestamps as expired so cleanup cannot be bypassed."""

    expiry = now - timedelta(days=GUEST_ACCESS_TTL_DAYS)
    created = _parse_guest_timestamp(created_at)
    last_seen = _parse_guest_timestamp(last_seen_at)
    return (
        created is None
        or last_seen is None
        or created < expiry
        or last_seen < expiry
    )


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


def _rebuild_dataclass_list(cls: type, raw: object, defaults: dict[str, Any] | None = None) -> list:
    """Rehydrate append-only nested plan fields with legacy-safe defaults."""

    result = []
    for item in _list_value(raw):
        if isinstance(item, cls):
            result.append(item)
            continue
        if not isinstance(item, dict):
            continue
        values = _field_values(cls, item, defaults)
        for key, value in list(values.items()):
            if key.endswith("_ids"):
                values[key] = _list_value(value)
        result.append(cls(**values))
    return result


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
            "place_id": "",
            "opening_window_id": "",
            "booking_requirement_id": "",
            "buffer_minutes": 0,
            "walking_minutes": None,
            "transit_minutes": None,
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

    route_plans = []
    for raw_route in payload.get("route_plans") or []:
        if isinstance(raw_route, dict):
            route_values = _field_values(
                RoutePlan,
                raw_route,
                {
                    "mode": "walking",
                    "origin": "",
                    "destination": "",
                    "duration": "",
                    "distance": "",
                    "summary": "",
                    "origin_address": "",
                    "destination_address": "",
                    "origin_location": "",
                    "destination_location": "",
                    "polyline": [],
                    "duration_minutes": None,
                    "distance_meters": None,
                    "estimated_cost": None,
                    "cost_currency": "",
                    "cost_scope": "",
                    "origin_place_id": "",
                    "destination_place_id": "",
                    "source_ids": [],
                    "departure_bucket": "",
                },
            )
            normalized_polyline: list[list[float]] = []
            for raw_point in _list_value(route_values.get("polyline")):
                if isinstance(raw_point, (list, tuple)) and len(raw_point) == 2:
                    try:
                        longitude = float(raw_point[0])
                        latitude = float(raw_point[1])
                    except (TypeError, ValueError):
                        continue
                    if (
                        longitude == longitude
                        and latitude == latitude
                        and -180 <= longitude <= 180
                        and -90 <= latitude <= 90
                    ):
                        normalized_polyline.append([round(longitude, 6), round(latitude, 6)])
            route_values["polyline"] = normalized_polyline
            route_values["source_ids"] = _list_value(route_values.get("source_ids"))
            route_plans.append(RoutePlan(**route_values))

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

    transport_pages = []
    for raw_page in payload.get("transport_pages") or []:
        if isinstance(raw_page, dict):
            transport_pages.append(
                TransportPage(
                    mode=str(raw_page.get("mode") or ""),
                    offset=max(0, int(raw_page.get("offset") or 0)),
                    limit=max(1, int(raw_page.get("limit") or 5)),
                    returned_count=max(0, int(raw_page.get("returned_count") or 0)),
                    total_count=max(0, int(raw_page.get("total_count") or 0)),
                    has_more=bool(raw_page.get("has_more")),
                    filter=str(raw_page.get("filter") or "all"),
                )
            )

    first_day = days[0].date if days else ""
    last_day = days[-1].date if days else first_day
    route_segments = _rebuild_dataclass_list(
        RouteSegment,
        payload.get("route_segments"),
        {
            "segment_id": "",
            "date": first_day,
            "origin_place_id": "",
            "destination_place_id": "",
            "mode": "unknown",
            "distance_meters": None,
            "duration_minutes": None,
            "estimated_cost": None,
            "buffer_minutes": 20,
            "walking_minutes": None,
            "source_ids": [],
        },
    )

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
            "destination_scope": "unknown",
            "destination_cities": [],
            "travelers": 1,
            "preferences": [],
            "summary": "",
            "out_of_range_items": [],
            "transport_options": [],
            "transport_pages": [],
            "route_plans": [],
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
            "route_segments": [],
            "optimization_objective": "balanced",
            "optimization_score": None,
            "place_candidates": [],
            "opening_windows": [],
            "booking_requirements": [],
            "daily_weather": [],
            "care_reminders": [],
            "unresolved_places": [],
            "optimization_diagnostics": [],
            "provider_meta": {},
            "schedule_constraints": [],
            "transport_edges": [],
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
    values["transport_pages"] = transport_pages
    values["route_plans"] = route_plans
    values["destination_cities"] = _list_value(values.get("destination_cities"))
    values["route_segments"] = route_segments
    values["transport_edges"] = _rebuild_dataclass_list(
        TransportEdge,
        values.get("transport_edges"),
        {
            "origin_city": "",
            "destination_city": "",
            "mode": "",
            "depart_at": "",
            "arrive_at": "",
            "duration_minutes": None,
            "price": None,
            "transfer_count": 0,
            "source_ids": [],
        },
    )
    values["place_candidates"] = _rebuild_dataclass_list(
        PlaceCandidate,
        values.get("place_candidates"),
        {
            "candidate_id": "",
            "provider_id": "",
            "name": "",
            "category": "",
            "province": "",
            "city": "",
            "district": "",
            "address": "",
            "location": "",
            "distance_from_city_center": "",
            "opening_status": "unknown",
            "confidence": "unknown",
            "source_ids": [],
            "query_text": "",
        },
    )
    values["opening_windows"] = _rebuild_dataclass_list(
        OpeningWindow,
        values.get("opening_windows"),
        {"date": "", "target_id": "", "open_time": "", "close_time": "", "last_entry_time": None, "closed_reason": None, "source_ids": []},
    )
    values["booking_requirements"] = _rebuild_dataclass_list(
        BookingRequirement,
        values.get("booking_requirements"),
        {"target_id": "", "required": False, "booking_url": None, "booking_note": "", "booking_status": "unknown", "verification": "unknown", "source_ids": []},
    )
    values["daily_weather"] = _rebuild_dataclass_list(
        DailyWeather,
        values.get("daily_weather"),
        {"date": "", "day_weather": "", "night_weather": "", "day_temp_c": None, "night_temp_c": None, "rain_probability": None, "wind_level": "", "humidity": "", "source_id": "", "retrieved_at": ""},
    )
    values["care_reminders"] = _rebuild_dataclass_list(
        CareReminder,
        values.get("care_reminders"),
        {"date": "", "message": "", "rule": "", "source_ids": []},
    )
    values["optimization_diagnostics"] = _rebuild_dataclass_list(
        OptimizationDiagnostic,
        values.get("optimization_diagnostics"),
        {"code": "unknown", "message": "", "severity": "info", "details": {}},
    )
    values["schedule_constraints"] = _rebuild_dataclass_list(
        ScheduleConstraint,
        values.get("schedule_constraints"),
        {"constraint_id": "", "constraint_type": "unknown", "target_id": "", "hard": False, "start_at": None, "end_at": None, "status": "unknown", "source_ids": []},
    )
    raw_meta = values.get("provider_meta")
    provider_meta = {}
    if isinstance(raw_meta, dict):
        for key, raw_item in raw_meta.items():
            if isinstance(raw_item, ProviderMeta):
                provider_meta[str(key)] = raw_item
            elif isinstance(raw_item, dict):
                provider_meta[str(key)] = ProviderMeta(
                    **_field_values(
                        ProviderMeta,
                        raw_item,
                        {
                            "provider": str(key),
                            "status": "not_requested",
                            "retryable": False,
                            "error_code": "",
                            "attempts": 0,
                            "latency_ms": 0,
                            "retrieved_at": "",
                            "valid_until": "",
                        },
                    )
                )
    values["provider_meta"] = provider_meta
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
                    last_seen_at TEXT NOT NULL,
                    cleanup_state TEXT NOT NULL DEFAULT 'active'
                );

                CREATE TABLE IF NOT EXISTS travel_plan_shares (
                    share_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    owner_id INTEGER,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
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
            guest_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(travel_guest_access)").fetchall()
            }
            if "cleanup_state" not in guest_columns:
                connection.execute(
                    "ALTER TABLE travel_guest_access ADD COLUMN cleanup_state TEXT NOT NULL DEFAULT 'active'"
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
            connection.execute("DELETE FROM travel_plan_shares WHERE thread_id = ?", (thread_id,))
            row = connection.execute(
                "SELECT plan_id FROM travel_plans WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if row is not None:
                connection.execute("DELETE FROM travel_plans WHERE thread_id = ?", (thread_id,))

    def has_thread_data(self, thread_id: str) -> bool:
        """Return whether the travel store has durable data for a thread."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM travel_plans WHERE thread_id = ?
                UNION ALL
                SELECT 1 FROM travel_turns WHERE thread_id = ?
                LIMIT 1
                """,
                (thread_id, thread_id),
            ).fetchone()
        return row is not None

    def validate_guest_access(
        self,
        thread_id: str,
        presented_token: str = "",
        *,
        touch: bool = False,
    ) -> bool:
        """Verify a live guest capability without creating a new one.

        ``touch`` is used by an authenticated claim flow only after the
        caller has already presented the HttpOnly capability.  Anonymous
        request authorization continues to use ``ensure_guest_access``.
        """

        presented_token = (presented_token or "").strip()
        now = datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT token_hash, created_at, last_seen_at, cleanup_state
                FROM travel_guest_access
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
            if row is None or str(row["cleanup_state"] or "active") != "active":
                return False
            created_at = _parse_guest_timestamp(row["created_at"])
            last_seen_at = _parse_guest_timestamp(row["last_seen_at"])
            expiry = now - timedelta(days=GUEST_ACCESS_TTL_DAYS)
            if (
                created_at is None
                or last_seen_at is None
                or created_at < expiry
                or last_seen_at < expiry
                or not presented_token
                or not hmac.compare_digest(str(row["token_hash"]), _hash_guest_token(presented_token))
            ):
                return False
            if touch:
                connection.execute(
                    "UPDATE travel_guest_access SET last_seen_at = ? WHERE thread_id = ?",
                    (_now_utc(), thread_id),
                )
        return True

    def claim_guest_thread(
        self,
        thread_id: str,
        user_id: int,
        presented_token: str = "",
    ) -> bool:
        """Transfer an active guest thread's durable travel data to a user.

        The capability is required even though the request is authenticated:
        knowing a client-generated ``guest_`` id is not proof of ownership.
        The transfer is idempotent for the same account and removes the guest
        capability once the data has been assigned.
        """

        presented_token = (presented_token or "").strip()
        now = datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT token_hash, created_at, last_seen_at, cleanup_state
                FROM travel_guest_access
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
            if row is None or str(row["cleanup_state"] or "active") != "active":
                return False
            created_at = _parse_guest_timestamp(row["created_at"])
            last_seen_at = _parse_guest_timestamp(row["last_seen_at"])
            expiry = now - timedelta(days=GUEST_ACCESS_TTL_DAYS)
            if (
                created_at is None
                or last_seen_at is None
                or created_at < expiry
                or last_seen_at < expiry
                or not presented_token
                or not hmac.compare_digest(str(row["token_hash"]), _hash_guest_token(presented_token))
            ):
                return False

            owner_rows = (
                connection.execute(
                    "SELECT user_id FROM travel_plans WHERE thread_id = ?",
                    (thread_id,),
                ).fetchall()
                + connection.execute(
                    "SELECT user_id FROM travel_turns WHERE thread_id = ?",
                    (thread_id,),
                ).fetchall()
            )
            stored_owners = {
                int(owner_row["user_id"])
                for owner_row in owner_rows
                if owner_row["user_id"] is not None
            }
            if stored_owners and stored_owners != {int(user_id)}:
                return False

            connection.execute(
                "UPDATE travel_plans SET user_id = ? WHERE thread_id = ? AND user_id IS NULL",
                (int(user_id), thread_id),
            )
            connection.execute(
                "UPDATE travel_turns SET user_id = ? WHERE thread_id = ? AND user_id IS NULL",
                (int(user_id), thread_id),
            )
            connection.execute(
                "UPDATE travel_plan_shares SET owner_id = ? WHERE thread_id = ? AND owner_id IS NULL",
                (int(user_id), thread_id),
            )
            connection.execute("DELETE FROM travel_guest_access WHERE thread_id = ?", (thread_id,))
        return True

    def list_guest_cleanup_candidates(self, now: datetime | None = None) -> list[str]:
        """Return guest threads that are expired or already being cleaned."""

        current = now or datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT thread_id, created_at, last_seen_at, cleanup_state
                FROM travel_guest_access
                """
            ).fetchall()
        candidates: list[tuple[datetime, str]] = []
        for row in rows:
            thread_id = str(row["thread_id"])
            state = str(row["cleanup_state"] or "active")
            expired = _guest_access_is_expired(row["created_at"], row["last_seen_at"], current)
            if state != "deleting" and not expired:
                continue
            sort_time = _parse_guest_timestamp(row["last_seen_at"]) or datetime.min.replace(
                tzinfo=timezone.utc
            )
            candidates.append((sort_time, thread_id))
        candidates.sort(key=lambda item: item[0])
        return [thread_id for _sort_time, thread_id in candidates]

    def begin_guest_cleanup(self, thread_id: str, now: datetime | None = None) -> bool:
        """Mark an expired guest capability as deleting, preventing new access."""

        current = now or datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT created_at, last_seen_at, cleanup_state
                FROM travel_guest_access
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
            if row is None:
                return False
            state = str(row["cleanup_state"] or "active")
            if state == "deleting":
                return True
            if not _guest_access_is_expired(row["created_at"], row["last_seen_at"], current):
                return False
            connection.execute(
                "UPDATE travel_guest_access SET cleanup_state = 'deleting' WHERE thread_id = ?",
                (thread_id,),
            )
        return True

    def finalize_guest_cleanup(self, thread_id: str) -> dict[str, Any] | None:
        """Delete travel-store data after checkpoint cleanup has succeeded."""

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT cleanup_state FROM travel_guest_access WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if row is None or str(row["cleanup_state"] or "active") != "deleting":
                return None

            owner_rows = (
                connection.execute(
                    "SELECT user_id FROM travel_plans WHERE thread_id = ?",
                    (thread_id,),
                ).fetchall()
                + connection.execute(
                    "SELECT user_id FROM travel_turns WHERE thread_id = ?",
                    (thread_id,),
                ).fetchall()
            )
            if any(owner_row["user_id"] is not None for owner_row in owner_rows):
                # A claim should remove the guest capability first.  Fail
                # closed if an old/manual row violates that invariant.
                connection.execute("DELETE FROM travel_guest_access WHERE thread_id = ?", (thread_id,))
                return None

            attachment_keys: set[str] = set()
            for raw in connection.execute(
                "SELECT attachments FROM travel_turns WHERE thread_id = ?", (thread_id,)
            ).fetchall():
                try:
                    attachments = json.loads(str(raw["attachments"] or "[]"))
                except json.JSONDecodeError:
                    attachments = []
                if not isinstance(attachments, list):
                    continue
                for attachment in attachments:
                    if isinstance(attachment, dict):
                        key = str(attachment.get("object_key") or "").strip()
                        if key and attachment.get("storage") == "oss":
                            attachment_keys.add(key)

            connection.execute("DELETE FROM travel_turns WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM travel_plan_shares WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM travel_guest_access WHERE thread_id = ?", (thread_id,))
            connection.execute("DELETE FROM travel_plans WHERE thread_id = ?", (thread_id,))
        return {"thread_id": thread_id, "attachment_keys": sorted(attachment_keys)}

    def create_plan_share(
        self,
        plan: TravelPlan,
        *,
        owner_id: int | None = None,
        expires_days: int = SHARE_DEFAULT_TTL_DAYS,
    ) -> dict[str, Any]:
        """Create an expiring, immutable read-only snapshot of a plan."""

        if not isinstance(expires_days, int) or not 1 <= expires_days <= SHARE_MAX_TTL_DAYS:
            raise ValueError(f"share expiration must be between 1 and {SHARE_MAX_TTL_DAYS} days")
        payload = _as_dict(plan)
        share_id = f"share_{secrets.token_hex(12)}"
        token = secrets.token_urlsafe(32)
        created_at = _now_utc()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(
            timespec="seconds"
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO travel_plan_shares
                    (share_id, token_hash, thread_id, plan_id, version, payload, owner_id,
                     created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    share_id,
                    _hash_guest_token(token),
                    plan.thread_id,
                    plan.plan_id,
                    int(plan.version),
                    _json(payload),
                    None if owner_id is None else int(owner_id),
                    created_at,
                    expires_at,
                ),
            )
        return {
            "share_id": share_id,
            "token": token,
            "thread_id": plan.thread_id,
            "plan_id": plan.plan_id,
            "version": int(plan.version),
            "created_at": created_at,
            "expires_at": expires_at,
        }

    def get_plan_share(self, token: str) -> dict[str, Any] | None:
        """Resolve a public share token without exposing the source thread."""

        token = (token or "").strip()
        if not token:
            return None
        now_label = _now_utc()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT share_id, thread_id, plan_id, version, payload, created_at, expires_at
                FROM travel_plan_shares
                WHERE token_hash = ?
                  AND revoked_at IS NULL
                  AND expires_at > ?
                """,
                (_hash_guest_token(token), now_label),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload"] or "{}"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        plan = _rebuild_plan(payload)
        return {
            "share_id": str(row["share_id"]),
            "thread_id": str(row["thread_id"]),
            "plan_id": str(row["plan_id"]),
            "version": int(row["version"]),
            "created_at": str(row["created_at"] or ""),
            "expires_at": str(row["expires_at"] or ""),
            "plan": plan,
        }

    def list_plan_shares(self, thread_id: str, owner_id: int | None = None) -> list[dict[str, Any]]:
        """List share metadata for an already-authorized source thread."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT share_id, plan_id, version, created_at, expires_at, revoked_at, owner_id
                FROM travel_plan_shares
                WHERE thread_id = ?
                ORDER BY created_at DESC
                """,
                (thread_id,),
            ).fetchall()
        result = []
        for row in rows:
            stored_owner = row["owner_id"]
            if stored_owner is not None and (owner_id is None or int(stored_owner) != int(owner_id)):
                continue
            result.append(
                {
                    "share_id": str(row["share_id"]),
                    "plan_id": str(row["plan_id"]),
                    "version": int(row["version"]),
                    "created_at": str(row["created_at"] or ""),
                    "expires_at": str(row["expires_at"] or ""),
                    "revoked_at": str(row["revoked_at"] or "") or None,
                }
            )
        return result

    def revoke_plan_share(
        self,
        thread_id: str,
        share_id: str,
        owner_id: int | None = None,
    ) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT owner_id, revoked_at
                FROM travel_plan_shares
                WHERE share_id = ? AND thread_id = ?
                """,
                (share_id, thread_id),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                return False
            stored_owner = row["owner_id"]
            if stored_owner is not None and (owner_id is None or int(stored_owner) != int(owner_id)):
                return False
            connection.execute(
                "UPDATE travel_plan_shares SET revoked_at = ? WHERE share_id = ?",
                (_now_utc(), share_id),
            )
        return True

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
                """
                SELECT token_hash, created_at, last_seen_at, cleanup_state
                FROM travel_guest_access
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
            if row is not None:
                if str(row["cleanup_state"] or "active") != "active":
                    return None
                now = datetime.now(timezone.utc)
                created_at = _parse_guest_timestamp(row["created_at"])
                last_seen_at = _parse_guest_timestamp(row["last_seen_at"])
                if created_at is None or last_seen_at is None:
                    return None
                expiry = now - timedelta(days=GUEST_ACCESS_TTL_DAYS)
                if created_at < expiry or last_seen_at < expiry:
                    # Empty guest threads can start over with a fresh
                    # capability. Durable data remains behind the expired
                    # row for the background cleanup worker and cannot be
                    # silently rebound by an arbitrary caller.
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


def validate_guest_access(
    thread_id: str,
    presented_token: str = "",
    *,
    touch: bool = False,
) -> bool:
    return travel_plan_store.validate_guest_access(thread_id, presented_token, touch=touch)


def claim_guest_thread(thread_id: str, user_id: int, presented_token: str = "") -> bool:
    return travel_plan_store.claim_guest_thread(thread_id, user_id, presented_token)


def has_travel_thread_data(thread_id: str) -> bool:
    return travel_plan_store.has_thread_data(thread_id)


def list_guest_cleanup_candidates(now: datetime | None = None) -> list[str]:
    return travel_plan_store.list_guest_cleanup_candidates(now=now)


def begin_guest_cleanup(thread_id: str, now: datetime | None = None) -> bool:
    return travel_plan_store.begin_guest_cleanup(thread_id, now=now)


def finalize_guest_cleanup(thread_id: str) -> dict[str, Any] | None:
    return travel_plan_store.finalize_guest_cleanup(thread_id)


def create_plan_share(
    plan: TravelPlan,
    *,
    owner_id: int | None = None,
    expires_days: int = SHARE_DEFAULT_TTL_DAYS,
) -> dict[str, Any]:
    return travel_plan_store.create_plan_share(
        plan,
        owner_id=owner_id,
        expires_days=expires_days,
    )


def get_plan_share(token: str) -> dict[str, Any] | None:
    return travel_plan_store.get_plan_share(token)


def list_plan_shares(thread_id: str, owner_id: int | None = None) -> list[dict[str, Any]]:
    return travel_plan_store.list_plan_shares(thread_id, owner_id=owner_id)


def revoke_plan_share(thread_id: str, share_id: str, owner_id: int | None = None) -> bool:
    return travel_plan_store.revoke_plan_share(thread_id, share_id, owner_id=owner_id)


def delete_travel_thread(thread_id: str, user_id: int | None = None) -> None:
    travel_plan_store.delete_thread(thread_id, user_id=user_id)


def serialize_plan(plan: TravelPlan | None) -> dict[str, Any] | None:
    return asdict(plan) if plan is not None else None
