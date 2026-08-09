from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from adapters.variflight_adapter import (
    is_variflight_configured,
    search_variflight_flights,
)


class FlightQueryError(RuntimeError):
    pass


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BRIDGE_PATH = BASE_DIR / "bridges" / "flight_mcp_bridge.py"


def _default_bridge_command() -> str:
    if DEFAULT_BRIDGE_PATH.exists():
        return f'"{sys.executable}" "{DEFAULT_BRIDGE_PATH}"'
    return ""


def _flight_mcp_mode() -> str:
    explicit_mode = os.getenv("FLIGHT_MCP_MODE", "").strip().lower()
    if explicit_mode:
        return explicit_mode
    enabled = os.getenv("FLIGHT_MCP_ENABLED", "").strip().lower()
    if enabled in {"1", "true", "yes", "on"} and _default_bridge_command():
        return "auto"
    return ""


def is_flight_mcp_enabled() -> bool:
    mode = _flight_mcp_mode()
    if mode == "variflight":
        # An explicitly selected provider is not usable until its own
        # credentials are valid.  This lets the planner expose
        # ``not_configured`` instead of a misleading provider failure.
        return is_variflight_configured()
    return bool(mode)


def _normalize_flight_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("flights", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _search_flights_via_command(
    origin: str,
    destination: str,
    date: str,
    bridge_mode: str = "auto",
) -> list[dict]:
    configured_command = os.getenv("FLIGHT_MCP_COMMAND", "").strip()
    command = configured_command or _default_bridge_command()
    if not command:
        raise FlightQueryError("FLIGHT_MCP_COMMAND is empty")

    timeout_seconds = int(os.getenv("FLIGHT_MCP_TIMEOUT_SECONDS", "45"))
    env = os.environ.copy()
    if bridge_mode:
        env["FLIGHT_BRIDGE_MODE"] = bridge_mode

    # The bundled bridge is a trusted local Python file, so execute it
    # directly.  Custom commands remain operator-configured shell commands.
    if configured_command:
        command_args = command
        use_shell = True
    else:
        command_args = [sys.executable, str(DEFAULT_BRIDGE_PATH)]
        use_shell = False

    process_kwargs = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "shell": use_shell,
        "env": env,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        process_kwargs["start_new_session"] = True

    process = subprocess.Popen(command_args, **process_kwargs)
    request_payload = json.dumps(
        {
            "origin": origin,
            "destination": destination,
            "date": date,
        },
        ensure_ascii=True,
    )
    try:
        stdout, stderr = process.communicate(input=request_payload, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise FlightQueryError(f"Flight MCP command timed out after {timeout_seconds} seconds") from exc

    if process.returncode != 0:
        stderr = (stderr or "").strip()
        stdout = (stdout or "").strip()
        message = ""
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                message = str(payload.get("error") or payload.get("message") or "")
        raise FlightQueryError(
            message
            or stderr
            or f"Flight MCP command failed with exit code {process.returncode}"
        )

    stdout = (stdout or "").strip()
    if not stdout:
        return []

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FlightQueryError(f"Flight MCP returned invalid JSON: {exc}") from exc

    return _normalize_flight_items(payload)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Terminate only the process group created for this provider call."""

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def search_flights(origin: str, destination: str, date: str) -> list[dict]:
    if not is_flight_mcp_enabled():
        return []

    mode = _flight_mcp_mode()
    if mode == "variflight":
        return search_variflight_flights(origin, destination, date)

    if mode in {"command", "auto", "package", "http", "dummy", "ctrip_h5"}:
        bridge_mode = "auto" if mode == "command" else mode
        return _search_flights_via_command(origin, destination, date, bridge_mode=bridge_mode)

    raise FlightQueryError(f"Unsupported FLIGHT_MCP_MODE: {mode}")
