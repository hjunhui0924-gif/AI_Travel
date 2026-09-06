"""Cooperative cancellation for in-flight chat generations.

The HTTP client can close an SSE stream immediately, but synchronous model and
provider adapters may still be running in the server worker.  This small
in-process registry lets the cancel request signal a running generation at
safe orchestration boundaries.  It intentionally does not pretend to abort a
network call that the underlying provider cannot cancel.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
CONTROL_TTL_SECONDS = 60 * 60
MAX_CONTROLS = 512


class GenerationCancelled(RuntimeError):
    """Raised when a generation reaches a cooperative cancellation point."""


@dataclass(slots=True)
class GenerationControl:
    request_id: str
    thread_id: str
    user_id: int | None
    created_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)

    def check(self) -> None:
        if self.event.is_set():
            raise GenerationCancelled("generation cancelled by user")


_controls: dict[str, GenerationControl] = {}
_controls_lock = threading.RLock()


def _is_valid_request_id(request_id: str) -> bool:
    return bool(REQUEST_ID_PATTERN.fullmatch(str(request_id or "").strip()))


def _prune_controls(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    expired = [
        request_id
        for request_id, control in _controls.items()
        if current - control.created_at > CONTROL_TTL_SECONDS
    ]
    for request_id in expired:
        _controls.pop(request_id, None)
    if len(_controls) <= MAX_CONTROLS:
        return
    oldest = sorted(_controls.values(), key=lambda item: item.created_at)
    for control in oldest[: len(_controls) - MAX_CONTROLS]:
        _controls.pop(control.request_id, None)


def register_generation(
    request_id: str,
    thread_id: str,
    user_id: int | None,
) -> GenerationControl | None:
    cleaned_id = str(request_id or "").strip()
    if not _is_valid_request_id(cleaned_id):
        return None
    control = GenerationControl(
        request_id=cleaned_id,
        thread_id=str(thread_id or ""),
        user_id=None if user_id is None else int(user_id),
    )
    with _controls_lock:
        _prune_controls()
        _controls[cleaned_id] = control
    return control


def cancel_generation(
    request_id: str,
    thread_id: str,
    user_id: int | None,
) -> bool:
    cleaned_id = str(request_id or "").strip()
    with _controls_lock:
        _prune_controls()
        control = _controls.get(cleaned_id)
        if control is None:
            return False
        if control.thread_id != str(thread_id or ""):
            return False
        normalized_user_id = None if user_id is None else int(user_id)
        if control.user_id != normalized_user_id:
            return False
        control.event.set()
        return True


def unregister_generation(request_id: str, control: GenerationControl | None) -> None:
    if control is None:
        return
    with _controls_lock:
        if _controls.get(str(request_id or "").strip()) is control:
            _controls.pop(str(request_id or "").strip(), None)

