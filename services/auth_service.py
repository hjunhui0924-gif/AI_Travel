from __future__ import annotations

import functools
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


BASE_DIR = Path(__file__).resolve().parent.parent
RESOURCES_DIR = BASE_DIR / "resources"
RESOURCES_DIR.mkdir(exist_ok=True)
DB_PATH = RESOURCES_DIR / "ai_agent_threads.db"
DEFAULT_THREAD_TITLE = "新对话"
SESSION_TTL_DAYS = 30

connection = sqlite3.connect(DB_PATH, check_same_thread=False)
connection.row_factory = sqlite3.Row
connection.execute("PRAGMA foreign_keys = ON")
_connection_lock = threading.RLock()


def _synchronized(func):
    """Serialize access to the process-wide SQLite connection.

    The auth store predates the request-threaded FastAPI app and intentionally
    keeps one connection for its small local database.  ``check_same_thread``
    makes that connection usable from worker threads, but it does not make
    transactions thread-safe; a lock is therefore required around every
    public operation that touches it.  RLock keeps nested helpers such as
    ``create_or_update_user -> create_user`` safe.
    """

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        with _connection_lock:
            return func(*args, **kwargs)

    return wrapped


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_label() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 200_000)
    return f"{salt_bytes.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, _digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    recalculated = _hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(recalculated, stored)


def _avatar_label(display_name: str, username: str) -> str:
    source = (display_name or username or "U").strip()
    return source[:1].upper()


def _serialize_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"] or row["username"]),
        "avatar_label": _avatar_label(str(row["display_name"] or ""), str(row["username"])),
        "created_at": str(row["created_at"] or ""),
    }


@_synchronized
def init_auth_store() -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chat_threads (
            thread_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    connection.commit()


def validate_registration_input(username: str, password: str, display_name: str) -> str | None:
    normalized_username = _normalize_username(username)
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,32}", normalized_username):
        return "用户名需为 3-32 位，只能包含字母、数字、下划线、点和中划线。"
    if len(password or "") < 6:
        return "密码至少需要 6 位。"
    if len((display_name or "").strip()) > 32:
        return "显示名称不能超过 32 个字符。"
    return None


@_synchronized
def create_user(username: str, password: str, display_name: str = "") -> dict[str, Any]:
    error = validate_registration_input(username, password, display_name or username)
    if error:
        raise ValueError(error)

    normalized_username = _normalize_username(username)
    resolved_display_name = (display_name or normalized_username).strip() or normalized_username
    now = _utc_now_label()
    try:
        cursor = connection.execute(
            """
            INSERT INTO users (username, password_hash, display_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (normalized_username, _hash_password(password), resolved_display_name, now),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("该用户名已存在。") from exc

    row = connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    user = _serialize_user(row)
    if not user:
        raise RuntimeError("创建用户失败。")
    return user


@_synchronized
def create_or_update_user(username: str, password: str, display_name: str = "") -> dict[str, Any]:
    error = validate_registration_input(username, password, display_name or username)
    if error:
        raise ValueError(error)

    normalized_username = _normalize_username(username)
    resolved_display_name = (display_name or normalized_username).strip() or normalized_username
    now = _utc_now_label()
    existing = connection.execute("SELECT id FROM users WHERE username = ?", (normalized_username,)).fetchone()
    if existing is None:
        return create_user(normalized_username, password, resolved_display_name)

    connection.execute(
        """
        UPDATE users
        SET password_hash = ?, display_name = ?, created_at = COALESCE(created_at, ?)
        WHERE username = ?
        """,
        (_hash_password(password), resolved_display_name, now, normalized_username),
    )
    connection.commit()
    row = connection.execute("SELECT * FROM users WHERE username = ?", (normalized_username,)).fetchone()
    user = _serialize_user(row)
    if not user:
        raise RuntimeError("更新用户失败。")
    return user


@_synchronized
def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    normalized_username = _normalize_username(username)
    row = connection.execute("SELECT * FROM users WHERE username = ?", (normalized_username,)).fetchone()
    if row is None:
        return None
    if not _verify_password(password, str(row["password_hash"])):
        return None
    return _serialize_user(row)


@_synchronized
def _get_user_row_by_id(user_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


@_synchronized
def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    return _serialize_user(_get_user_row_by_id(user_id))


@_synchronized
def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    connection.execute(
        """
        INSERT INTO user_sessions (user_id, token_hash, created_at, expires_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            _hash_token(token),
            now.isoformat(timespec="seconds"),
            expires_at.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
        ),
    )
    connection.commit()
    return token


@_synchronized
def delete_session(token: str) -> None:
    if not token:
        return
    connection.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_hash_token(token),))
    connection.commit()


@_synchronized
def get_user_by_session_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None

    now_label = _utc_now().isoformat(timespec="seconds")
    row = connection.execute(
        """
        SELECT users.*
        FROM user_sessions
        JOIN users ON users.id = user_sessions.user_id
        WHERE user_sessions.token_hash = ?
          AND user_sessions.expires_at > ?
        """,
        (_hash_token(token), now_label),
    ).fetchone()
    if row is None:
        connection.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_hash_token(token),))
        connection.commit()
        return None

    connection.execute(
        "UPDATE user_sessions SET last_seen_at = ? WHERE token_hash = ?",
        (now_label, _hash_token(token)),
    )
    connection.commit()
    return _serialize_user(row)


@_synchronized
def create_thread(user_id: int, title: str = DEFAULT_THREAD_TITLE, thread_id: str | None = None) -> dict[str, Any]:
    resolved_thread_id = (thread_id or f"thread_{secrets.token_hex(8)}").strip()
    now = _utc_now_label()
    connection.execute(
        """
        INSERT INTO chat_threads (thread_id, user_id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (resolved_thread_id, user_id, (title or DEFAULT_THREAD_TITLE).strip() or DEFAULT_THREAD_TITLE, now, now),
    )
    connection.commit()
    return {
        "thread_id": resolved_thread_id,
        "title": (title or DEFAULT_THREAD_TITLE).strip() or DEFAULT_THREAD_TITLE,
        "created_at": now,
        "updated_at": now,
    }


@_synchronized
def get_thread(thread_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM chat_threads WHERE thread_id = ?", (thread_id,)).fetchone()
    if row is None:
        return None
    return {
        "thread_id": str(row["thread_id"]),
        "user_id": int(row["user_id"]),
        "title": str(row["title"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


@_synchronized
def ensure_thread_for_user(user_id: int, thread_id: str, title: str = DEFAULT_THREAD_TITLE) -> bool:
    existing = get_thread(thread_id)
    if existing is None:
        create_thread(user_id=user_id, title=title, thread_id=thread_id)
        return True
    return int(existing["user_id"]) == int(user_id)


@_synchronized
def claim_thread_for_user(
    user_id: int,
    thread_id: str,
    title: str = DEFAULT_THREAD_TITLE,
) -> tuple[dict[str, Any], bool] | None:
    """Attach a previously anonymous/checkpoint thread to an account.

    The caller must prove ownership of the anonymous capability before
    invoking this function.  The returned boolean tells the caller whether a
    new account-thread row was created so it can compensate if a second
    storage transfer fails.
    """

    cleaned_thread_id = str(thread_id or "").strip()
    if not cleaned_thread_id or len(cleaned_thread_id) > 256:
        return None
    existing = get_thread(cleaned_thread_id)
    if existing is not None:
        if int(existing["user_id"]) != int(user_id):
            return None
        cleaned_title = (title or "").strip()
        if cleaned_title and existing["title"] == DEFAULT_THREAD_TITLE:
            update_thread_activity(int(user_id), cleaned_thread_id, cleaned_title)
        return get_thread(cleaned_thread_id), False

    created = create_thread(
        user_id=int(user_id),
        title=(title or DEFAULT_THREAD_TITLE).strip()[:32] or DEFAULT_THREAD_TITLE,
        thread_id=cleaned_thread_id,
    )
    return created, True


@_synchronized
def list_threads_for_user(user_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT thread_id, title, created_at, updated_at
        FROM chat_threads
        WHERE user_id = ?
        ORDER BY updated_at DESC, created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "thread_id": str(row["thread_id"]),
            "title": str(row["title"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


@_synchronized
def update_thread_activity(user_id: int, thread_id: str, title: str | None = None) -> None:
    existing = get_thread(thread_id)
    if existing is None or int(existing["user_id"]) != int(user_id):
        return

    now = _utc_now_label()
    next_title = str(existing["title"])
    cleaned_title = (title or "").strip()
    if cleaned_title and next_title == DEFAULT_THREAD_TITLE:
        next_title = cleaned_title[:32]

    connection.execute(
        "UPDATE chat_threads SET title = ?, updated_at = ? WHERE thread_id = ?",
        (next_title, now, thread_id),
    )
    connection.commit()


@_synchronized
def backfill_titles_for_user(user_id: int, title_resolver: Callable[[str], str | None]) -> None:
    rows = connection.execute(
        "SELECT thread_id, title FROM chat_threads WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    for row in rows:
        if str(row["title"]) != DEFAULT_THREAD_TITLE:
            continue
        resolved = (title_resolver(str(row["thread_id"])) or "").strip()
        if not resolved:
            continue
        connection.execute(
            "UPDATE chat_threads SET title = ? WHERE thread_id = ?",
            (resolved[:32], str(row["thread_id"])),
        )
    connection.commit()


@_synchronized
def list_legacy_thread_ids() -> list[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT source.thread_id
        FROM (
            SELECT thread_id FROM checkpoints
            UNION
            SELECT thread_id FROM writes
        ) AS source
        LEFT JOIN chat_threads ON chat_threads.thread_id = source.thread_id
        WHERE chat_threads.thread_id IS NULL
        ORDER BY source.thread_id
        """
    ).fetchall()
    return [str(row["thread_id"]) for row in rows if str(row["thread_id"]).strip()]


@_synchronized
def assign_threads_to_user(user_id: int, thread_ids: list[str], default_title: str = DEFAULT_THREAD_TITLE) -> int:
    now = _utc_now_label()
    claimed = 0
    for thread_id in thread_ids:
        cleaned_thread_id = str(thread_id or "").strip()
        if not cleaned_thread_id:
            continue
        existing = get_thread(cleaned_thread_id)
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO chat_threads (thread_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cleaned_thread_id, user_id, default_title, now, now),
        )
        claimed += 1
    connection.commit()
    return claimed


@_synchronized
def delete_thread_record(user_id: int, thread_id: str) -> bool:
    existing = get_thread(thread_id)
    if existing is None or int(existing["user_id"]) != int(user_id):
        return False
    connection.execute("DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,))
    connection.commit()
    return True


@_synchronized
def is_thread_owned_by_user(user_id: int, thread_id: str) -> bool:
    existing = get_thread(thread_id)
    if existing is None:
        return False
    return int(existing["user_id"]) == int(user_id)


init_auth_store()
