import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel

from adapters.amap_adapter import fetch_static_route_map, render_route_map_svg
from agents.agent import (
    attach_assistant_metadata,
    consume_activity_log,
    consume_source_cards,
    consume_travel_plan,
    delete_checkpoint_thread,
    delete_thread,
    derive_session_title,
    encode_assistant_metadata,
    get_messages,
    has_checkpoint_data,
    stream_chat,
)
from services.auth_service import (
    DEFAULT_THREAD_TITLE,
    authenticate_user,
    assign_threads_to_user,
    claim_thread_for_user,
    create_session,
    create_thread,
    create_user,
    delete_session,
    delete_thread_record,
    ensure_thread_for_user,
    get_user_by_session_token,
    get_thread,
    is_thread_owned_by_user,
    list_threads_for_user,
    list_legacy_thread_ids,
    update_thread_activity,
)
from agents.travel_agent import plan_travel, render_travel_response
from services.travel_store import (
    GUEST_ACCESS_TTL_DAYS,
    PlanVersionConflict,
    begin_guest_cleanup,
    claim_guest_thread,
    create_plan_share,
    finalize_guest_cleanup,
    get_current_plan,
    get_plan_version,
    get_plan_owner_id,
    get_plan_share,
    ensure_guest_access,
    has_travel_thread_data,
    list_guest_cleanup_candidates,
    list_plan_shares,
    list_plan_versions,
    revoke_plan_share,
    save_conversation_turn,
    save_plan_version,
    save_travel_turn,
    validate_guest_access,
)
from utils.file_utils import UnsupportedFileTypeError, parse_uploads
from utils.oss_utils import delete_oss_object
from services.travel_exports import render_plan_markdown, render_shared_plan_html
from services.answer_citations import (
    deduplicate_sources,
    parse_answer_citations,
    strip_citation_markers_for_stream,
    validate_answer_segments,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SESSION_COOKIE_NAME = "ai_agent_session"
GUEST_COOKIE_NAME = "ai_agent_guest"
COOKIE_SECURE = (
    os.getenv("AI_AGENT_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}
    or os.getenv("APP_ENV", "").strip().lower() in {"prod", "production"}
    or os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}
)
GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * GUEST_ACCESS_TTL_DAYS
try:
    GUEST_CLEANUP_INTERVAL_SECONDS = max(
        60, int(os.getenv("AI_AGENT_GUEST_CLEANUP_INTERVAL_SECONDS", "3600"))
    )
except ValueError:
    GUEST_CLEANUP_INTERVAL_SECONDS = 3600

logger = logging.getLogger("ai_agent")
INTERNAL_SSE_ERROR_MESSAGE = "服务暂时不可用，请稍后重试。"


def _history_attachment_keys(thread_id: str) -> set[str]:
    keys: set[str] = set()
    try:
        messages = get_messages(thread_id)
    except Exception:
        messages = []
    for message in messages:
        for attachment in message.get("attachments", []):
            if not isinstance(attachment, dict):
                continue
            if attachment.get("storage") == "oss" and attachment.get("object_key"):
                keys.add(str(attachment["object_key"]))
    return keys


def cleanup_expired_guest_sessions() -> int:
    """Remove expired guest data and its checkpoint state.

    Travel-store cleanup is marked before the checkpoint is removed.  A
    failed checkpoint delete leaves the row in ``cleanup_state=deleting`` so
    the next worker pass can retry instead of silently losing the cleanup
    candidate.
    """

    deleted = 0
    for thread_id in list_guest_cleanup_candidates():
        if not begin_guest_cleanup(thread_id):
            continue
        attachment_keys = _history_attachment_keys(thread_id)
        try:
            delete_checkpoint_thread(thread_id)
        except Exception:
            logger.exception("guest checkpoint cleanup failed for %s", thread_id)
            continue
        result = finalize_guest_cleanup(thread_id)
        if result is None:
            continue
        attachment_keys.update(result.get("attachment_keys", []))
        for object_key in attachment_keys:
            delete_oss_object(object_key)
        deleted += 1
    return deleted


async def _guest_cleanup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_expired_guest_sessions)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("guest cleanup pass failed")
        await asyncio.sleep(GUEST_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    cleanup_task = asyncio.create_task(_guest_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)

app = FastAPI(title="AI Agent", lifespan=app_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def attach_guest_access_cookie(request: Request, call_next):
    response = await call_next(request)
    if getattr(request.state, "guest_access_cleared", False):
        response.delete_cookie(GUEST_COOKIE_NAME, path="/")
        return response
    guest_token = getattr(request.state, "guest_access_token", "")
    if guest_token:
        response.set_cookie(
            key=GUEST_COOKIE_NAME,
            value=guest_token,
            httponly=True,
            samesite="lax",
            secure=COOKIE_SECURE,
            max_age=GUEST_COOKIE_MAX_AGE,
            path="/",
        )
    return response


def extract_renderable_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)
    return "".join(parts)


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _current_user_from_request(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    user = get_user_by_session_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录。")
    return user


def _auth_json_response(
    user: dict,
    session_token: str,
    *,
    claimed_thread: dict | None = None,
    guest_claim_status: str | None = None,
) -> JSONResponse:
    payload: dict = {"status": "success", "user": user}
    if claimed_thread is not None:
        payload["claimed_thread"] = claimed_thread
    if guest_claim_status is not None:
        payload["guest_claim"] = {"status": guest_claim_status}
    response = JSONResponse(payload)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return response


def _is_guest_thread_id(thread_id: str) -> bool:
    cleaned = (thread_id or "").strip().lower()
    # Guest IDs are client-generated and older checkpoints used UUIDs with
    # hyphens (and occasionally a readable suffix).  Keep the namespace
    # deliberately narrow without requiring one exact generation format.
    return bool(re.fullmatch(r"guest_[a-z0-9_-]{1,128}", cleaned))


def _is_migratable_guest_thread_id(thread_id: str) -> bool:
    cleaned = (thread_id or "").strip().lower()
    return _is_guest_thread_id(cleaned) and len(cleaned.removeprefix("guest_")) >= 16


def _authorized_thread_user(request: Request, thread_id: str) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    user = get_user_by_session_token(token)
    if user is None:
        if not _is_guest_thread_id(thread_id):
            raise HTTPException(status_code=401, detail="请先登录。")
        # A guest-shaped id is an anonymous capability only.  It must never
        # become an alternate path to a logged-in user's thread or plan.
        if get_thread(thread_id) is not None or get_plan_owner_id(thread_id) is not None:
            raise HTTPException(status_code=404, detail="无权访问该会话。")
        presented_guest_token = request.cookies.get(GUEST_COOKIE_NAME, "")
        # Legacy checkpoints predate capability binding.  A caller must not
        # be able to mint the first capability merely by supplying an
        # arbitrary cookie; an existing, previously bound capability may
        # still be verified below.
        has_legacy_checkpoint = has_checkpoint_data(thread_id)
        guest_token = ensure_guest_access(
            thread_id,
            presented_guest_token,
            allow_new_binding=not has_legacy_checkpoint,
        )
        if not guest_token:
            raise HTTPException(status_code=404, detail="无权访问该会话。")
        # Re-issue the cookie on every valid request so browser max-age
        # follows the same seven-day inactivity window as the server.
        request.state.guest_access_token = guest_token
        return None
    # A deliberately migrated legacy checkpoint may retain its old guest_ ID
    # while now being owned by this account.  The ownership check remains the
    # authority; only an unowned guest-shaped ID is rejected here.
    thread_owned = is_thread_owned_by_user(int(user["id"]), thread_id)
    if _is_guest_thread_id(thread_id) and not thread_owned:
        raise HTTPException(status_code=400, detail="登录后请使用账户会话 ID，不能使用 guest 会话 ID。")
    if not thread_owned:
        raise HTTPException(status_code=404, detail="无权访问该会话。")
    return user


def _try_claim_guest_thread(
    request: Request,
    user: dict,
    thread_id: str,
    requested_title: str = "",
) -> tuple[dict | None, str]:
    """Claim an active guest thread after login, failing closed."""

    cleaned_thread_id = str(thread_id or "").strip()
    if not _is_migratable_guest_thread_id(cleaned_thread_id):
        return None, "not_requested"
    presented_token = request.cookies.get(GUEST_COOKIE_NAME, "")
    if not validate_guest_access(cleaned_thread_id, presented_token):
        # The two SQLite stores cannot commit atomically.  If a process was
        # interrupted after the account row and travel/checkpoint ownership
        # were transferred, the guest capability has already been removed.
        # Treat that exact account-owned, data-bearing state as an idempotent
        # successful claim so the next login can finish the handoff and clear
        # the stale browser cookie.
        existing_thread = get_thread(cleaned_thread_id)
        if existing_thread and int(existing_thread["user_id"]) == int(user["id"]):
            try:
                has_data = has_checkpoint_data(cleaned_thread_id) or has_travel_thread_data(
                    cleaned_thread_id
                )
            except Exception:
                has_data = False
            if has_data:
                request.state.guest_access_cleared = True
                return existing_thread, "claimed"
        return None, "expired_or_not_owned"
    try:
        has_data = has_checkpoint_data(cleaned_thread_id) or has_travel_thread_data(cleaned_thread_id)
    except Exception:
        has_data = False
    if not has_data:
        return None, "empty"

    title = (requested_title or "").strip()[:32]
    if not title:
        try:
            title = derive_session_title(get_messages(cleaned_thread_id))[:32]
        except Exception:
            title = ""
    title = title or DEFAULT_THREAD_TITLE

    account_thread = claim_thread_for_user(int(user["id"]), cleaned_thread_id, title)
    if account_thread is None:
        return None, "already_owned"
    thread, created = account_thread
    try:
        claimed = claim_guest_thread(cleaned_thread_id, int(user["id"]), presented_token)
    except Exception:
        if created:
            delete_thread_record(int(user["id"]), cleaned_thread_id)
        raise
    if not claimed:
        if created:
            delete_thread_record(int(user["id"]), cleaned_thread_id)
        return None, "expired_or_not_owned"
    request.state.guest_access_cleared = True
    return thread, "claimed"


class TravelReplanRequest(BaseModel):
    message: str
    search_enabled: bool = False
    expected_version: int | None = None


class TravelItemUpdateRequest(BaseModel):
    locked: bool | None = None
    status: str | None = None
    expected_version: int | None = None


class LegacyThreadMigrationRequest(BaseModel):
    # Migration is deliberately opt-in and requires IDs already known by the
    # user (for example, from their old browser state).  The server never
    # auto-assigns every orphaned checkpoint to the current account.
    thread_ids: list[str]


class PlanShareCreateRequest(BaseModel):
    version: int | None = None
    expires_days: int = 30


def _serialize_plan_for_api(plan, *, include_days: bool = True) -> dict:
    payload = asdict(plan)
    if not include_days:
        payload["days"] = []
    return payload


@app.get("/")
def read_root():
    with open(STATIC_DIR / "index.html", "r", encoding="utf-8") as file:
        return HTMLResponse(content=file.read())


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "travel-mark.png")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/auth/me")
def auth_me(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    user = get_user_by_session_token(token)
    return {"status": "success", "authenticated": bool(user), "user": user}


@app.post("/auth/register")
async def auth_register(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
    guest_thread_id: str = Form(""),
    guest_title: str = Form(""),
):
    try:
        user = create_user(username=username, password=password, display_name=display_name)
        session_token = create_session(int(user["id"]))
        try:
            claimed_thread, claim_status = _try_claim_guest_thread(
                request, user, guest_thread_id, guest_title
            )
        except Exception:
            logger.exception("guest claim failed during registration")
            claimed_thread, claim_status = None, "failed"
        response = _auth_json_response(
            user,
            session_token,
            claimed_thread=claimed_thread,
            guest_claim_status=claim_status if guest_thread_id else None,
        )
        if claimed_thread is not None:
            response.delete_cookie(GUEST_COOKIE_NAME, path="/")
        return response
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


@app.post("/auth/login")
async def auth_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    guest_thread_id: str = Form(""),
    guest_title: str = Form(""),
):
    user = authenticate_user(username=username, password=password)
    if not user:
        return JSONResponse({"status": "error", "message": "用户名或密码不正确。"}, status_code=401)
    session_token = create_session(int(user["id"]))
    try:
        claimed_thread, claim_status = _try_claim_guest_thread(
            request, user, guest_thread_id, guest_title
        )
    except Exception:
        logger.exception("guest claim failed during login")
        claimed_thread, claim_status = None, "failed"
    response = _auth_json_response(
        user,
        session_token,
        claimed_thread=claimed_thread,
        guest_claim_status=claim_status if guest_thread_id else None,
    )
    if claimed_thread is not None:
        response.delete_cookie(GUEST_COOKIE_NAME, path="/")
    return response


@app.post("/auth/logout")
def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    delete_session(token)
    response = JSONResponse({"status": "success"})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.post("/threads")
def create_chat_thread(request: Request):
    try:
        user = _current_user_from_request(request)
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    thread = create_thread(int(user["id"]), DEFAULT_THREAD_TITLE)
    return {"status": "success", "thread": thread}


@app.post("/threads/migrate-legacy")
def migrate_legacy_threads(payload: LegacyThreadMigrationRequest, request: Request):
    try:
        user = _current_user_from_request(request)
        requested: list[str] = []
        for raw_thread_id in payload.thread_ids[:100]:
            thread_id = str(raw_thread_id or "").strip()
            if not thread_id or len(thread_id) > 256 or thread_id in requested:
                continue
            requested.append(thread_id)
        if not requested:
            return JSONResponse(
                {"status": "error", "message": "请提供要迁移的旧会话 ID。"},
                status_code=400,
            )

        legacy_ids = set(list_legacy_thread_ids())
        # Checkpoint storage has no owner metadata.  Only the random guest
        # namespace is safe to self-claim; ambiguous legacy IDs such as
        # "default" require an out-of-band recovery process.
        eligible = [
            thread_id
            for thread_id in requested
            if thread_id in legacy_ids and _is_migratable_guest_thread_id(thread_id)
        ]
        skipped = [thread_id for thread_id in requested if thread_id not in eligible]
        assign_threads_to_user(int(user["id"]), eligible, DEFAULT_THREAD_TITLE)
        migrated = [
            thread_id
            for thread_id in eligible
            if is_thread_owned_by_user(int(user["id"]), thread_id)
        ]
        return {
            "status": "success",
            "migrated_thread_ids": migrated,
            "skipped_thread_ids": [thread_id for thread_id in requested if thread_id not in migrated],
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/sessions")
def get_sessions(request: Request):
    try:
        user = _current_user_from_request(request)
        return {"status": "success", "sessions": list_threads_for_user(int(user["id"]))}
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/history/{thread_id}")
def get_history(thread_id: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        return {
            "status": "success",
            "messages": get_messages(thread_id, user_id=int(user["id"]) if user else None),
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except PermissionError:
        return JSONResponse({"status": "error", "message": "无权访问该会话。"}, status_code=404)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.delete("/history/{thread_id}")
def clear_history(thread_id: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        if user is not None and not delete_thread_record(int(user["id"]), thread_id):
            return JSONResponse({"status": "error", "message": "无权删除该会话。"}, status_code=404)
        delete_thread(thread_id, user_id=int(user["id"]) if user else None)
        return {"status": "success"}
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except PermissionError:
        return JSONResponse({"status": "error", "message": "无权访问该会话。"}, status_code=404)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/travel/plans/{thread_id}")
def get_travel_plan(
    thread_id: str,
    request: Request,
    include_days: bool = Query(True),
):
    try:
        user = _authorized_thread_user(request, thread_id)
        plan = get_current_plan(thread_id, user_id=int(user["id"]) if user else None)
        return {
            "status": "success",
            "plan": _serialize_plan_for_api(plan, include_days=include_days) if plan else None,
            "versions": list_plan_versions(thread_id, user_id=int(user["id"]) if user else None),
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/travel/plans/{thread_id}/export")
def export_travel_plan(
    thread_id: str,
    request: Request,
    format: str = Query("markdown"),
    version: int | None = Query(None, ge=1),
):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        plan = (
            get_plan_version(thread_id, version, user_id=user_id)
            if version is not None
            else get_current_plan(thread_id, user_id=user_id)
        )
        if plan is None:
            return JSONResponse({"status": "error", "message": "travel plan not found"}, status_code=404)

        export_format = str(format or "").strip().lower()
        filename_base = re.sub(r"[^A-Za-z0-9_-]+", "_", plan.plan_id or "travel_plan")[:80]
        if export_format in {"md", "markdown"}:
            content = render_plan_markdown(plan)
            media_type = "text/markdown; charset=utf-8"
            filename = f"{filename_base}.md"
        elif export_format == "json":
            content = json.dumps(asdict(plan), ensure_ascii=False, indent=2) + "\n"
            media_type = "application/json; charset=utf-8"
            filename = f"{filename_base}.json"
        else:
            return JSONResponse(
                {"status": "error", "message": "format 只支持 markdown 或 json。"},
                status_code=400,
            )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/travel/plans/{thread_id}/map")
def get_travel_plan_map(
    thread_id: str,
    request: Request,
    version: int | None = Query(None, ge=1),
):
    """Return a server-rendered route image for stored route geometry.

    The Web Service key stays on the server. AMap Static Map is preferred; a
    verified SVG route schematic is returned when that optional permission is
    unavailable, so the UI never loses the route preview.
    """

    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        plan = (
            get_plan_version(thread_id, version, user_id=user_id)
            if version is not None
            else get_current_plan(thread_id, user_id=user_id)
        )
        if plan is None:
            return JSONResponse({"status": "error", "message": "该会话还没有旅行计划。"}, status_code=404)

        routes = [
            asdict(route)
            for route in plan.route_plans
            if len(route.polyline) >= 2
        ]
        if not routes:
            return JSONResponse(
                {"status": "error", "message": "当前计划没有可展示的路线几何。"},
                status_code=404,
            )

        try:
            rendered = fetch_static_route_map(routes)
        except Exception:
            logger.warning("AMap Static Map unavailable; using route SVG fallback")
            rendered = None
        if rendered is None:
            fallback = render_route_map_svg(routes)
            if not fallback:
                return JSONResponse(
                    {"status": "error", "message": "当前计划没有可展示的路线几何。"},
                    status_code=404,
                )
            return Response(
                content=fallback,
                media_type="image/svg+xml",
                headers={"Cache-Control": "no-store", "X-Route-Map-Fallback": "true"},
            )
        content, media_type = rendered
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception:
        logger.exception("route map rendering failed")
        return JSONResponse(
            {"status": "error", "message": "路线地图暂时不可用，请稍后重试。"},
            status_code=502,
        )


@app.post("/travel/plans/{thread_id}/shares")
def create_travel_plan_share(
    thread_id: str,
    payload: PlanShareCreateRequest,
    request: Request,
):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        plan = (
            get_plan_version(thread_id, payload.version, user_id=user_id)
            if payload.version is not None
            else get_current_plan(thread_id, user_id=user_id)
        )
        if plan is None:
            return JSONResponse({"status": "error", "message": "travel plan not found"}, status_code=404)
        created = create_plan_share(
            plan,
            owner_id=user_id,
            expires_days=payload.expires_days,
        )
        token = str(created.pop("token"))
        created["url"] = f"/shared/{token}"
        created["api_url"] = f"/shared/plans/{token}"
        return {"status": "success", "share": created}
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/travel/plans/{thread_id}/shares")
def get_travel_plan_shares(thread_id: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        return {
            "status": "success",
            "shares": list_plan_shares(thread_id, owner_id=user_id),
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.delete("/travel/plans/{thread_id}/shares/{share_id}")
def revoke_travel_plan_share(thread_id: str, share_id: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        if not revoke_plan_share(thread_id, share_id, owner_id=user_id):
            return JSONResponse({"status": "error", "message": "分享链接不存在或已失效。"}, status_code=404)
        return {"status": "success"}
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/shared/plans/{token}")
def get_public_shared_plan(token: str):
    shared = get_plan_share(token)
    if shared is None:
        return JSONResponse({"status": "error", "message": "分享链接不存在或已失效。"}, status_code=404)
    plan = shared["plan"]
    public_plan = asdict(plan)
    # A share token is the public capability; do not disclose the private
    # source thread id in the unauthenticated response.
    public_plan["thread_id"] = ""
    return {
        "status": "success",
        "share": {
            "share_id": shared["share_id"],
            "version": shared["version"],
            "created_at": shared["created_at"],
            "expires_at": shared["expires_at"],
        },
        "plan": public_plan,
    }


@app.get("/shared/{token}")
def render_public_shared_plan(token: str):
    shared = get_plan_share(token)
    if shared is None:
        return HTMLResponse(
            "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>分享已失效</title>"
            "<p>分享链接不存在或已失效。</p>",
            status_code=404,
        )
    response = HTMLResponse(render_shared_plan_html(shared["plan"], shared["expires_at"]))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/travel/plans/{thread_id}/calendar")
def get_travel_calendar(
    thread_id: str,
    request: Request,
    version: int | None = Query(None, ge=1),
):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        plan = (
            get_plan_version(thread_id, version, user_id=user_id)
            if version is not None
            else get_current_plan(thread_id, user_id=user_id)
        )
        if plan is None:
            return {"status": "success", "plan_id": None, "version": None, "days": []}
        return {
            "status": "success",
            "plan_id": plan.plan_id,
            "version": plan.version,
            "timezone": plan.timezone,
            "requested_days": plan.requested_days,
            "projected_days": plan.projected_days,
            "calendar_truncated": plan.calendar_truncated,
            "projection_end_date": plan.projection_end_date,
            "out_of_range_item_count": len(plan.out_of_range_items),
            "days": [
                {
                    "date": day.date,
                    "day_number": day.day_number,
                    "title": day.title,
                    "summary": day.summary,
                    "item_count": len(day.items),
                    "has_conflicts": day.has_conflicts,
                }
                for day in plan.days
            ],
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/travel/plans/{thread_id}/days/{date_text}")
def get_travel_day(
    thread_id: str,
    date_text: str,
    request: Request,
    version: int | None = Query(None, ge=1),
):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        plan = (
            get_plan_version(thread_id, version, user_id=user_id)
            if version is not None
            else get_current_plan(thread_id, user_id=user_id)
        )
        if plan is None:
            return JSONResponse({"status": "error", "message": "该会话还没有旅行计划。"}, status_code=404)
        day = next((item for item in plan.days if item.date == date_text), None)
        if day is None:
            return JSONResponse({"status": "error", "message": "该日期不在当前旅行计划中。"}, status_code=404)
        return {"status": "success", "plan_id": plan.plan_id, "version": plan.version, "day": asdict(day)}
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/travel/plans/{thread_id}/versions/{version}")
def get_travel_plan_version(
    thread_id: str,
    version: int,
    request: Request,
    include_days: bool = Query(True),
):
    try:
        user = _authorized_thread_user(request, thread_id)
        plan = get_plan_version(thread_id, version, user_id=int(user["id"]) if user else None)
        if plan is None:
            return JSONResponse({"status": "error", "message": "计划版本不存在。"}, status_code=404)
        return {"status": "success", "plan": _serialize_plan_for_api(plan, include_days=include_days)}
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.patch("/travel/plans/{thread_id}/items/{item_id}")
def update_travel_item(
    thread_id: str,
    item_id: str,
    payload: TravelItemUpdateRequest,
    request: Request,
):
    allowed_statuses = {"suggested", "confirmed", "booked", "skipped", "cancelled"}
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        current_plan = get_current_plan(thread_id, user_id=user_id)
        if current_plan is None:
            return JSONResponse({"status": "error", "message": "该会话还没有旅行计划。"}, status_code=404)
        if payload.locked is None and payload.status is None:
            return JSONResponse({"status": "error", "message": "请提供 locked 或 status。"}, status_code=400)
        if payload.status is not None and payload.status not in allowed_statuses:
            return JSONResponse({"status": "error", "message": "不支持的计划项状态。"}, status_code=400)
        if payload.status in {"confirmed", "booked"} and payload.locked is False:
            return JSONResponse(
                {"status": "error", "message": "confirmed/booked 计划项必须保持 locked=true。"},
                status_code=400,
            )
        if payload.locked is True and payload.status not in {None, "confirmed", "booked"}:
            return JSONResponse(
                {"status": "error", "message": "只有 confirmed/booked 计划项可以被锁定。"},
                status_code=400,
            )

        updated_plan = deepcopy(current_plan)
        target = None
        for day in updated_plan.days:
            for item in day.items:
                if item.item_id == item_id:
                    target = item
                    break
            if target is not None:
                break
        if target is None:
            target = next(
                (item for item in updated_plan.out_of_range_items if item.item_id == item_id),
                None,
            )
        if target is None:
            return JSONResponse({"status": "error", "message": "计划项不存在。"}, status_code=404)

        if payload.status is not None:
            target.status = payload.status
            if payload.status in {"confirmed", "booked"}:
                target.locked = True
            elif payload.locked is None:
                target.locked = False
        if payload.locked is not None:
            target.locked = payload.locked
            if payload.status is None:
                target.status = "confirmed" if payload.locked else "suggested"

        saved_plan = save_plan_version(
            updated_plan,
            user_id=user_id,
            change_summary=f"更新计划项：{target.title}",
            expected_version=payload.expected_version if payload.expected_version is not None else current_plan.version,
        )
        return {
            "status": "success",
            "plan": asdict(saved_plan),
            "item": asdict(
                next(
                    item
                    for day in saved_plan.days
                    for item in day.items
                    if item.item_id == item_id
                )
                if any(item.item_id == item_id for day in saved_plan.days for item in day.items)
                else next(item for item in saved_plan.out_of_range_items if item.item_id == item_id)
            ),
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except PermissionError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=403)
    except PlanVersionConflict as exc:
        return JSONResponse(
            {
                "status": "error",
                "message": str(exc),
                "current_version": exc.current_version,
            },
            status_code=409,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/travel/plans/{thread_id}/replan")
def replan_travel_plan(thread_id: str, payload: TravelReplanRequest, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        user_id = int(user["id"]) if user else None
        current_plan = get_current_plan(thread_id, user_id=user_id)
        if current_plan is None:
            return JSONResponse({"status": "error", "message": "该会话还没有可重规划的旅行计划。"}, status_code=404)
        if not payload.message.strip():
            return JSONResponse({"status": "error", "message": "请描述想怎么调整行程。"}, status_code=400)

        response = plan_travel(
            payload.message.strip(),
            [],
            thread_id=thread_id,
            search_enabled=payload.search_enabled,
            current_plan=current_plan,
        )
        if response.trip_plan is None:
            return JSONResponse({"status": "error", "message": "本次没有生成新的旅行计划。"}, status_code=502)
        saved_plan = save_plan_version(
            response.trip_plan,
            user_id=user_id,
            change_summary=payload.message.strip()[:120],
            expected_version=payload.expected_version if payload.expected_version is not None else current_plan.version,
        )
        response.trip_plan = saved_plan
        response.sources = saved_plan.sources
        response.conflicts = saved_plan.conflicts
        source_dicts = deduplicate_sources([asdict(source) for source in saved_plan.sources])
        parsed_answer = parse_answer_citations(
            render_travel_response(response),
            source_dicts,
            citations_enabled=payload.search_enabled,
        )
        rendered = parsed_answer.final_text
        answer_segments = parsed_answer.answer_segments
        save_travel_turn(
            thread_id=thread_id,
            user_id=user_id,
            role="user",
            content=payload.message.strip(),
            search_enabled=payload.search_enabled,
        )
        save_travel_turn(
            thread_id=thread_id,
            user_id=user_id,
            role="assistant",
            content=rendered
            + encode_assistant_metadata(
                [],
                source_dicts,
                answer_segments,
                search_enabled=payload.search_enabled,
            ),
            search_enabled=payload.search_enabled,
            plan=saved_plan,
        )
        return {
            "status": "success",
            "plan": asdict(saved_plan),
            "final_text": rendered,
            "sources": source_dicts,
            "answer_segments": answer_segments,
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except PermissionError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=403)
    except PlanVersionConflict as exc:
        return JSONResponse(
            {
                "status": "error",
                "message": str(exc),
                "current_version": exc.current_version,
            },
            status_code=409,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/chat")
async def chat(
    request: Request,
    message: str = Form(""),
    thread_id: str = Form("default"),
    search_enabled: bool = Form(False),
    files: list[UploadFile] | None = File(default=None),
):
    try:
        # Use the same ownership gate as the travel/history endpoints.  A
        # guest-shaped thread is normally anonymous-only, but a guest thread
        # that was explicitly claimed during login remains addressable under
        # its original id so the user can continue the conversation without
        # losing the just-merged context.
        user = _authorized_thread_user(request, thread_id)
    except HTTPException as exc:
        detail = exc.detail
        if not _is_guest_thread_id(thread_id):
            async def unauthorized_response():
                yield _sse_event("error", {"message": detail})

            return StreamingResponse(unauthorized_response(), media_type="text/event-stream; charset=utf-8")
        user = None
        async def unauthorized_guest_response():
            yield _sse_event("error", {"message": detail})

        return StreamingResponse(unauthorized_guest_response(), media_type="text/event-stream; charset=utf-8")

    try:
        attachments = parse_uploads(files or [])
    except UnsupportedFileTypeError as exc:
        async def invalid_file_response():
            yield _sse_event("error", {"message": f"[文件类型不支持] {exc}"})

        return StreamingResponse(invalid_file_response(), media_type="text/event-stream; charset=utf-8")
    except Exception as exc:
        async def file_error_response():
            yield _sse_event("error", {"message": f"[文件解析失败] {exc}"})

        return StreamingResponse(file_error_response(), media_type="text/event-stream; charset=utf-8")

    if not message.strip() and not attachments:
        async def empty_response():
            yield _sse_event("error", {"message": "请输入问题，或上传文件/图片后再发送。"})

        return StreamingResponse(empty_response(), media_type="text/event-stream; charset=utf-8")

    if user is not None and not ensure_thread_for_user(int(user["id"]), thread_id, DEFAULT_THREAD_TITLE):
        async def forbidden_thread_response():
            yield _sse_event("error", {"message": "无权访问该会话。"})

        return StreamingResponse(forbidden_thread_response(), media_type="text/event-stream; charset=utf-8")

    def stream_generator():
        try:
            has_output = False
            raw_seen_text = ""
            emitted_text = ""
            assistant_activities = []
            assistant_sources = []
            assistant_answer_segments = []
            assistant_clarification = None
            assistant_scope_refusal = False
            travel_plan = None
            is_travel_response = False

            def safe_text_delta(raw_text: str) -> str:
                nonlocal emitted_text
                safe_text = strip_citation_markers_for_stream(raw_text)
                if not safe_text.startswith(emitted_text):
                    return ""
                delta = safe_text[len(emitted_text):]
                emitted_text = safe_text
                return delta

            for chunk, metadata in stream_chat(
                message=message,
                thread_id=thread_id,
                search_enabled=search_enabled,
                attachments=attachments,
                user_id=int(user["id"]) if user is not None else None,
            ):
                if isinstance(metadata, dict) and metadata.get("trip_plan"):
                    travel_plan = metadata["trip_plan"]
                if isinstance(metadata, dict) and metadata.get("travel"):
                    is_travel_response = True
                if isinstance(metadata, dict) and isinstance(metadata.get("answer_segments"), list):
                    if metadata["answer_segments"]:
                        assistant_answer_segments = metadata["answer_segments"]
                if isinstance(metadata, dict) and isinstance(metadata.get("clarification"), dict):
                    assistant_clarification = metadata["clarification"]
                if isinstance(metadata, dict) and metadata.get("scope_refusal") is True:
                    assistant_scope_refusal = True
                for activity in consume_activity_log():
                    assistant_activities.append(activity)
                    yield _sse_event("activity", activity)
                for source in consume_source_cards():
                    assistant_sources.append(source)

                if not isinstance(chunk, (AIMessageChunk, AIMessage)):
                    continue

                text = extract_renderable_content(chunk.content)
                if not text:
                    continue

                if isinstance(chunk, AIMessageChunk):
                    has_output = True
                    raw_seen_text += text
                    delta = safe_text_delta(raw_seen_text)
                    if delta:
                        yield _sse_event("text", {"delta": delta})
                    continue

                if not text.startswith(raw_seen_text):
                    has_output = True
                    raw_seen_text = text
                    delta = safe_text_delta(raw_seen_text)
                    if delta:
                        yield _sse_event("text", {"delta": delta})
                    continue

                delta = text[len(raw_seen_text):]
                if delta:
                    has_output = True
                    raw_seen_text = text
                    safe_delta = safe_text_delta(raw_seen_text)
                    if safe_delta:
                        yield _sse_event("text", {"delta": safe_delta})

            for activity in consume_activity_log():
                assistant_activities.append(activity)
                yield _sse_event("activity", activity)
            for source in consume_source_cards():
                assistant_sources.append(source)

            context_plan = consume_travel_plan()
            if context_plan:
                travel_plan = travel_plan or context_plan
            if travel_plan:
                for source in travel_plan.get("sources", []):
                    if not isinstance(source, dict):
                        continue
                    source_key = source.get("evidence_id") or source.get("url") or source.get("title")
                    if any(
                        (item.get("evidence_id") or item.get("url") or item.get("title")) == source_key
                        for item in assistant_sources
                    ):
                        continue
                    assistant_sources.append(source)

            assistant_sources = deduplicate_sources(assistant_sources)
            for source in assistant_sources:
                yield _sse_event("source", source)
            parsed_answer = parse_answer_citations(
                raw_seen_text,
                assistant_sources,
                citations_enabled=search_enabled,
            )
            final_text = parsed_answer.final_text or "暂时没有生成结果，请再试一次。"
            parsed_segments = parsed_answer.answer_segments
            if not parsed_segments and final_text:
                parsed_segments = [{"text": final_text, "source_ids": []}]

            validated_segments = validate_answer_segments(
                assistant_answer_segments,
                final_text,
                assistant_sources,
                citations_enabled=search_enabled,
            )
            answer_segments = (
                validated_segments
                if assistant_answer_segments and validated_segments
                else parsed_segments
            )
            if final_text.startswith(emitted_text) and final_text != emitted_text:
                yield _sse_event("text", {"delta": final_text[len(emitted_text):]})
                emitted_text = final_text

            seen_text_with_metadata = final_text + encode_assistant_metadata(
                assistant_activities,
                assistant_sources,
                answer_segments,
                search_enabled=search_enabled,
                clarification=assistant_clarification,
                scope_refusal=assistant_scope_refusal,
            )

            if not is_travel_response:
                try:
                    user_content = message.strip() or "请结合我上传的文件或图片回答。"
                    stored_attachments = [
                        {
                            "name": attachment.get("name", ""),
                            "extension": attachment.get("extension", ""),
                            "modality": attachment.get("modality", "text"),
                            "image_url": attachment.get("image_url"),
                            "storage": attachment.get("storage", ""),
                            "object_key": attachment.get("object_key", ""),
                        }
                        for attachment in attachments
                    ]
                    save_conversation_turn(
                        thread_id=thread_id,
                        user_id=int(user["id"]) if user is not None else None,
                        role="user",
                        content=user_content,
                        attachments=stored_attachments,
                        search_enabled=search_enabled,
                    )
                    assistant_content = seen_text_with_metadata or (
                        "暂时没有生成结果，请再试一次。"
                        + encode_assistant_metadata(
                            assistant_activities,
                            assistant_sources,
                            answer_segments,
                            search_enabled=search_enabled,
                        )
                    )
                    save_conversation_turn(
                        thread_id=thread_id,
                        user_id=int(user["id"]) if user is not None else None,
                        role="assistant",
                        content=assistant_content,
                        search_enabled=search_enabled,
                    )
                except Exception:
                    # The LangGraph checkpoint is still available as a
                    # fallback if the ordered history log cannot be written.
                    pass
            try:
                attach_assistant_metadata(
                    thread_id,
                    raw_seen_text,
                    assistant_activities,
                    assistant_sources,
                    answer_segments,
                    search_enabled=search_enabled,
                )
            except Exception:
                pass

            if user is not None:
                try:
                    update_thread_activity(int(user["id"]), thread_id, title=message.strip()[:32])
                except Exception:
                    pass

            if not has_output:
                yield _sse_event("text", {"delta": "暂时没有生成结果，请再试一次。"})

            yield _sse_event(
                "done",
                {
                    "ok": True,
                    "activities": assistant_activities,
                    "sources": assistant_sources,
                    # Internal history metadata is persisted separately and
                    # must never leak into the frontend's rendered text.
                    "final_text": final_text,
                    "answer_segments": answer_segments,
                    "clarification": assistant_clarification,
                    "scope_refusal": assistant_scope_refusal,
                    "trip_plan": travel_plan,
                    "attachments": [
                        {
                            "name": attachment["name"],
                            "modality": attachment.get("modality", "text"),
                        }
                        for attachment in attachments
                        if attachment.get("modality") == "text"
                    ],
                },
            )
        except Exception:
            # Do not expose provider URLs, credentials, filesystem paths, or
            # traceback text through the streaming protocol.  The server log
            # retains the diagnostic while the client receives a stable,
            # non-sensitive message.
            logger.exception("chat stream failed")
            yield _sse_event("error", {"message": INTERNAL_SSE_ERROR_MESSAGE})

    return StreamingResponse(stream_generator(), media_type="text/event-stream; charset=utf-8")


if __name__ == "__main__":
    import uvicorn

    try:
        server_port = int(os.getenv("AI_AGENT_PORT", "8001"))
    except ValueError:
        server_port = 8001
    uvicorn.run(app, host="127.0.0.1", port=server_port)
