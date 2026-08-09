import json
import re
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel

from agents.agent import (
    attach_assistant_metadata,
    consume_activity_log,
    consume_source_cards,
    consume_travel_plan,
    delete_thread,
    encode_assistant_metadata,
    get_messages,
    stream_chat,
)
from services.auth_service import (
    DEFAULT_THREAD_TITLE,
    authenticate_user,
    create_session,
    create_thread,
    create_user,
    delete_session,
    delete_thread_record,
    ensure_thread_for_user,
    get_user_by_session_token,
    is_thread_owned_by_user,
    list_threads_for_user,
    update_thread_activity,
)
from agents.travel_agent import plan_travel, render_travel_response
from services.travel_store import (
    get_current_plan,
    get_plan_version,
    list_plan_versions,
    save_plan_version,
    save_travel_turn,
)
from utils.file_utils import UnsupportedFileTypeError, parse_uploads

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SESSION_COOKIE_NAME = "ai_agent_session"

app = FastAPI(title="AI Agent")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


def _auth_json_response(user: dict, session_token: str) -> JSONResponse:
    response = JSONResponse({"status": "success", "user": user})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return response


def _is_guest_thread_id(thread_id: str) -> bool:
    cleaned = (thread_id or "").strip().lower()
    return cleaned.startswith("guest_") and bool(re.fullmatch(r"guest_[0-9a-f]+", cleaned))


def _authorized_thread_user(request: Request, thread_id: str) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    user = get_user_by_session_token(token)
    if user is None:
        if not _is_guest_thread_id(thread_id):
            raise HTTPException(status_code=401, detail="请先登录。")
        return None
    if not is_thread_owned_by_user(int(user["id"]), thread_id):
        raise HTTPException(status_code=404, detail="无权访问该会话。")
    return user


class TravelReplanRequest(BaseModel):
    message: str
    search_enabled: bool = False


class TravelItemUpdateRequest(BaseModel):
    locked: bool | None = None
    status: str | None = None


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
    username: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
):
    try:
        user = create_user(username=username, password=password, display_name=display_name)
        session_token = create_session(int(user["id"]))
        return _auth_json_response(user, session_token)
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


@app.post("/auth/login")
async def auth_login(
    username: str = Form(""),
    password: str = Form(""),
):
    user = authenticate_user(username=username, password=password)
    if not user:
        return JSONResponse({"status": "error", "message": "用户名或密码不正确。"}, status_code=401)
    session_token = create_session(int(user["id"]))
    return _auth_json_response(user, session_token)


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
        try:
            user = _current_user_from_request(request)
        except HTTPException:
            user = None
        if user is None:
            if not _is_guest_thread_id(thread_id):
                return JSONResponse({"status": "error", "message": "请先登录。"}, status_code=401)
        elif not is_thread_owned_by_user(int(user["id"]), thread_id):
            return JSONResponse({"status": "error", "message": "无权访问该会话。"}, status_code=404)
        return {"status": "success", "messages": get_messages(thread_id)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.delete("/history/{thread_id}")
def clear_history(thread_id: str, request: Request):
    try:
        try:
            user = _current_user_from_request(request)
        except HTTPException:
            user = None
        if user is None:
            if not _is_guest_thread_id(thread_id):
                return JSONResponse({"status": "error", "message": "请先登录。"}, status_code=401)
        elif not delete_thread_record(int(user["id"]), thread_id):
            return JSONResponse({"status": "error", "message": "无权删除该会话。"}, status_code=404)
        delete_thread(thread_id)
        return {"status": "success"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/travel/plans/{thread_id}")
def get_travel_plan(thread_id: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        plan = get_current_plan(thread_id, user_id=int(user["id"]) if user else None)
        return {
            "status": "success",
            "plan": asdict(plan) if plan else None,
            "versions": list_plan_versions(thread_id, user_id=int(user["id"]) if user else None),
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/travel/plans/{thread_id}/calendar")
def get_travel_calendar(thread_id: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        plan = get_current_plan(thread_id, user_id=int(user["id"]) if user else None)
        if plan is None:
            return {"status": "success", "plan_id": None, "version": None, "days": []}
        return {
            "status": "success",
            "plan_id": plan.plan_id,
            "version": plan.version,
            "timezone": plan.timezone,
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
def get_travel_day(thread_id: str, date_text: str, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        plan = get_current_plan(thread_id, user_id=int(user["id"]) if user else None)
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
def get_travel_plan_version(thread_id: str, version: int, request: Request):
    try:
        user = _authorized_thread_user(request, thread_id)
        plan = get_plan_version(thread_id, version, user_id=int(user["id"]) if user else None)
        if plan is None:
            return JSONResponse({"status": "error", "message": "计划版本不存在。"}, status_code=404)
        return {"status": "success", "plan": asdict(plan)}
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
        )
        return {
            "status": "success",
            "plan": asdict(saved_plan),
            "item": asdict(next(item for day in saved_plan.days for item in day.items if item.item_id == item_id)),
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except PermissionError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=403)
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
        )
        response.trip_plan = saved_plan
        response.sources = saved_plan.sources
        response.conflicts = saved_plan.conflicts
        rendered = render_travel_response(response)
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
            content=rendered,
            search_enabled=payload.search_enabled,
            plan=saved_plan,
        )
        return {
            "status": "success",
            "plan": asdict(saved_plan),
            "final_text": rendered,
            "sources": [asdict(source) for source in saved_plan.sources],
        }
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, status_code=exc.status_code)
    except PermissionError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=403)
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
        user = _current_user_from_request(request)
    except HTTPException as exc:
        if not _is_guest_thread_id(thread_id):
            async def unauthorized_response():
                yield _sse_event("error", {"message": exc.detail})

            return StreamingResponse(unauthorized_response(), media_type="text/event-stream; charset=utf-8")
        user = None

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
            seen_text = ""
            assistant_activities = []
            assistant_sources = []
            travel_plan = None

            for chunk, metadata in stream_chat(
                message=message,
                thread_id=thread_id,
                search_enabled=search_enabled,
                attachments=attachments,
                user_id=int(user["id"]) if user is not None else None,
            ):
                if isinstance(metadata, dict) and metadata.get("trip_plan"):
                    travel_plan = metadata["trip_plan"]
                for activity in consume_activity_log():
                    assistant_activities.append(activity)
                    yield _sse_event("activity", activity)
                for source in consume_source_cards():
                    assistant_sources.append(source)
                    yield _sse_event("source", source)

                if not isinstance(chunk, (AIMessageChunk, AIMessage)):
                    continue

                text = extract_renderable_content(chunk.content)
                if not text:
                    continue

                if isinstance(chunk, AIMessageChunk):
                    has_output = True
                    seen_text += text
                    yield _sse_event("text", {"delta": text})
                    continue

                if not text.startswith(seen_text):
                    has_output = True
                    seen_text = text
                    yield _sse_event("text", {"delta": text})
                    continue

                delta = text[len(seen_text):]
                if delta:
                    has_output = True
                    seen_text = text
                    yield _sse_event("text", {"delta": delta})

            for activity in consume_activity_log():
                assistant_activities.append(activity)
                yield _sse_event("activity", activity)
            for source in consume_source_cards():
                assistant_sources.append(source)
                yield _sse_event("source", source)

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
                    yield _sse_event("source", source)

            seen_text_with_metadata = seen_text + encode_assistant_metadata(assistant_activities, assistant_sources)
            try:
                attach_assistant_metadata(thread_id, seen_text, assistant_activities, assistant_sources)
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
                    "final_text": seen_text_with_metadata,
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
        except Exception as exc:
            yield _sse_event("error", {"message": f"[服务运行错误或网络超时: {exc}]"})

    return StreamingResponse(stream_generator(), media_type="text/event-stream; charset=utf-8")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
