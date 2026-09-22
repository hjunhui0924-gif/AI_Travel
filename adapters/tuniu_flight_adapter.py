"""Official Tuniu domestic-flight search adapter.

The project talks to Tuniu through the supported ``tuniu`` CLI.  The CLI
owns OAuth/API-Key handling, while this module only sends the read-only
``searchLowestPriceFlight`` request and normalizes its documented response.

This adapter deliberately does not expose cabin-detail, order, payment, or
cancel operations.  Search results are current candidates only; they never
mean that a seat was held, a ticket was issued, or an order was created.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import date, datetime, timedelta
from typing import Any


DEFAULT_CLI_COMMAND = "tuniu"
DEFAULT_TIMEOUT_SECONDS = 30


class TuniuFlightError(RuntimeError):
    """A Tuniu flight query failed without a safe result."""

    def __init__(self, message: str, *, failure_kind: str = "provider_error") -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class TuniuFlightResults(list[dict[str, Any]]):
    """Normalized flight rows plus provider pagination diagnostics."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        errors: list[str] | None = None,
        query_id: str = "",
        total_page_num: int = 1,
    ) -> None:
        super().__init__(items or [])
        self.errors = list(errors or [])
        self.query_id = query_id
        self.total_page_num = max(1, int(total_page_num or 1))


def is_tuniu_configured() -> bool:
    """Return whether a Tuniu credential mechanism is configured.

    API Key mode is configured through the environment. OAuth remains
    technically recognized by the adapter for backward compatibility, but is
    not selected by the project's default configuration.
    """

    auth_type = os.getenv("TUNIU_AUTH_TYPE", "").strip().lower()
    return bool(
        os.getenv("TUNIU_API_KEY", "").strip()
        or auth_type in {"auto", "oauth"}
        or (auth_type == "apikey" and os.getenv("TUNIU_API_KEY", "").strip())
    )


def _cli_tokens() -> list[str]:
    configured = os.getenv("TUNIU_CLI_COMMAND", "").strip() or DEFAULT_CLI_COMMAND
    try:
        tokens = shlex.split(configured, posix=os.name != "nt")
    except ValueError as exc:
        raise TuniuFlightError(
            "TUNIU_CLI_COMMAND 配置无法解析。",
            failure_kind="configuration",
        ) from exc
    tokens = [token.strip('"') for token in tokens if token.strip('"')]
    if not tokens:
        raise TuniuFlightError("TUNIU_CLI_COMMAND 为空。", failure_kind="configuration")
    # npm installs Windows command shims as ``.cmd`` files.  CreateProcess
    # does not apply PATHEXT when subprocess.run receives an argv list, so
    # resolve the executable explicitly before invoking it without a shell.
    executable = shutil.which(tokens[0])
    if executable:
        tokens[0] = executable
    return tokens


def _timeout_seconds() -> int:
    raw = os.getenv("TUNIU_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        return max(1, int(raw))
    except ValueError as exc:
        raise TuniuFlightError("TUNIU_TIMEOUT_SECONDS 必须是正整数。", failure_kind="configuration") from exc


def _parse_json_output(raw: str) -> object:
    text = (raw or "").strip()
    if not text:
        raise TuniuFlightError("途牛 CLI 没有返回内容。", failure_kind="schema_changed")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Keep the adapter tolerant of a harmless CLI log line before the
        # machine-readable response, but never manufacture a response.
        decoder = json.JSONDecoder()
        candidates: list[object] = []
        for index, character in enumerate(text):
            if character not in "[{":
                continue
            try:
                candidate, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            candidates.append(candidate)
        if candidates:
            return candidates[-1]
    raise TuniuFlightError("途牛 CLI 返回了无效 JSON。", failure_kind="schema_changed")


def _error_kind(error_type: str, code: object, message: str) -> str:
    marker = f"{error_type} {code} {message}".lower()
    if (
        "oauth" in marker
        or "login" in marker
        or "auth" in marker
        or "未授权" in marker
        or "授权" in marker
        or str(code) in {"104", "108", "109", "110", "111", "112"}
    ):
        return "unauthorized"
    if (
        "rate" in marker
        or "rpm" in marker
        or "rpd" in marker
        or "too many" in marker
        or "限流" in marker
        or str(code) == "429"
    ):
        return "rate_limited"
    return "provider_error"


def _raise_cli_error(payload: object, *, stderr: str = "", returncode: int | None = None) -> None:
    error_type = ""
    code: object = ""
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            error_type = str(error.get("type") or "")
            code = error.get("code", "")
            message = str(error.get("message") or error.get("msg") or "")
        elif error not in (None, ""):
            message = str(error)
        if not message:
            message = str(payload.get("message") or payload.get("msg") or "")
    message = message or (stderr or "途牛 CLI 调用失败").strip()
    kind = _error_kind(error_type, code, message)
    if kind == "unauthorized":
        if os.getenv("TUNIU_AUTH_TYPE", "").strip().lower() == "apikey":
            message = "途牛 API Key 未配置或无效，请检查 TUNIU_API_KEY。"
        else:
            message = "途牛 CLI 未授权，请配置 TUNIU_API_KEY 或检查认证配置。"
    elif kind == "rate_limited":
        message = "途牛航班接口已限流，请稍后再试。"
    elif returncode is not None and not message:
        message = f"途牛 CLI 退出码 {returncode}。"
    raise TuniuFlightError(
        message[:500],
        failure_kind=kind,
    )


def _unwrap_call_payload(payload: object) -> object:
    """Unwrap current CLI and Streamable HTTP response envelopes."""

    if isinstance(payload, dict) and payload.get("success") is False:
        _raise_cli_error(payload)
    if isinstance(payload, dict) and payload.get("success") is True and "result" in payload:
        payload = payload["result"]

    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]

    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        texts = [
            str(item.get("text") or "")
            for item in payload["content"]
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(part for part in texts if part).strip()
        if text:
            return _parse_json_output(text)
    return payload


def _call_cli(arguments: dict[str, Any]) -> object:
    timeout_seconds = _timeout_seconds()
    command = [
        *_cli_tokens(),
        "call",
        "flight",
        "searchLowestPriceFlight",
        "-a",
        json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        "-o",
        "json",
        "-t",
        str(timeout_seconds),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + 5,
            check=False,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise TuniuFlightError(
            "未找到途牛 CLI，请先执行 npm install -g tuniu-cli@latest。",
            failure_kind="not_configured",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TuniuFlightError(
            "途牛航班查询超时。",
            failure_kind="timeout",
        ) from exc
    except OSError as exc:
        raise TuniuFlightError(
            f"途牛 CLI 无法启动：{type(exc).__name__}。",
            failure_kind="not_configured",
        ) from exc

    raw_output = (completed.stdout or "").strip()
    payload: object | None = None
    if raw_output:
        try:
            payload = _parse_json_output(raw_output)
        except TuniuFlightError:
            if completed.returncode == 0:
                raise
    if completed.returncode != 0:
        _raise_cli_error(payload, stderr=(completed.stderr or "").strip(), returncode=completed.returncode)
    if payload is None:
        raise TuniuFlightError("途牛 CLI 没有返回航班响应。", failure_kind="schema_changed")
    return _unwrap_call_payload(payload)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _as_int(value: object) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _parse_datetime(value: object, fallback_date: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        try:
            base = date.fromisoformat(fallback_date)
        except ValueError:
            return None
        return parsed.replace(year=base.year, month=base.month, day=base.day)
    return None


def _format_money(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def _label_airport(airport: object, terminal: object) -> str:
    name = str(airport or "").strip()
    terminal_text = str(terminal or "").strip()
    return f"{name} {terminal_text}".strip()


def _normalize_flight(
    item: dict[str, Any],
    fallback_date: str,
    fallback_origin: str,
    fallback_destination: str,
) -> dict[str, Any] | None:
    flight_number = str(item.get("flightNumber") or "").strip()
    departure = _parse_datetime(item.get("departureTime"), fallback_date)
    arrival = _parse_datetime(item.get("arrivalTime"), fallback_date)
    if not flight_number or departure is None or arrival is None:
        return None
    if arrival < departure:
        arrival += timedelta(days=1)

    base_price = _as_number(item.get("basePrice"))
    total_tax = _as_number(item.get("totalTax"))
    if base_price is not None and total_tax is not None:
        total_price = base_price + total_tax
    else:
        total_price = base_price
    departure_airport = _label_airport(item.get("departureAirport"), item.get("departureTerminal"))
    arrival_airport = _label_airport(item.get("arrivalAirport"), item.get("arrivalTerminal"))
    airport_label = " -> ".join(part for part in (departure_airport, arrival_airport) if part)
    seat_count = _as_int(item.get("remainingSeats"))
    if seat_count is not None and seat_count < 0:
        seat_count = None

    summary_parts = []
    flight_type = str(item.get("type") or "").strip()
    craft_type = str(item.get("craftType") or "").strip()
    shared_flight = str(item.get("shareFlightNo") or "").strip()
    if flight_type:
        summary_parts.append(flight_type)
    if craft_type:
        summary_parts.append(f"机型 {craft_type}")
    if base_price is not None and total_tax is not None:
        summary_parts.append(f"票面 ¥{_format_money(base_price)} + 税费 ¥{_format_money(total_tax)}")
    if shared_flight:
        summary_parts.append(f"共享航班 {shared_flight}")

    return {
        "flight_no": flight_number,
        "origin": str(item.get("departureCityName") or fallback_origin).strip(),
        "destination": str(item.get("arrivalCityName") or fallback_destination).strip(),
        "depart_time": departure.strftime("%H:%M"),
        "arrive_time": arrival.strftime("%H:%M"),
        "depart_date": departure.date().isoformat(),
        "arrive_date": arrival.date().isoformat(),
        "duration": str(item.get("totalDuration") or item.get("flyTime") or "").strip(),
        "price": _format_money(total_price),
        "summary": " | ".join(summary_parts),
        "provider": "Tuniu",
        "airline": str(item.get("airlineCompany") or "").strip(),
        "cabin": str(item.get("cabinClass") or "").strip(),
        "airport": airport_label,
        "seat_count": seat_count,
        "is_demo": False,
    }


def search_tuniu_flights(origin: str, destination: str, date_text: str) -> TuniuFlightResults:
    """Search domestic flights through Tuniu's official CLI/MCP service."""

    origin = str(origin or "").strip()
    destination = str(destination or "").strip()
    date_text = str(date_text or "").strip()
    if not origin or not destination or not date_text:
        raise TuniuFlightError("出发城市、到达城市和日期不能为空。", failure_kind="invalid_input")
    try:
        date.fromisoformat(date_text)
    except ValueError as exc:
        raise TuniuFlightError("航班日期必须是 YYYY-MM-DD。", failure_kind="invalid_input") from exc

    payload = _call_cli(
        {
            "departureCityName": origin,
            "arrivalCityName": destination,
            "departureDate": date_text,
        }
    )
    if not isinstance(payload, dict):
        raise TuniuFlightError("途牛航班响应不是 JSON 对象。", failure_kind="schema_changed")
    if payload.get("successCode") is False:
        _raise_cli_error(payload)
    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        raise TuniuFlightError("途牛航班响应缺少 data 列表。", failure_kind="schema_changed")

    normalized: list[dict[str, Any]] = []
    malformed_rows = 0
    for item in raw_items:
        if not isinstance(item, dict):
            malformed_rows += 1
            continue
        result = _normalize_flight(item, date_text, origin, destination)
        if result is None:
            malformed_rows += 1
            continue
        normalized.append(result)
    if raw_items and malformed_rows == len(raw_items):
        raise TuniuFlightError("途牛航班行字段无法识别。", failure_kind="schema_changed")

    normalized.sort(
        key=lambda item: (
            _as_number(item.get("price")) if _as_number(item.get("price")) is not None else 10**9,
            item.get("depart_time", ""),
            item.get("flight_no", ""),
        )
    )
    errors = [f"provider_rows_schema_changed:{malformed_rows}"] if malformed_rows else []
    return TuniuFlightResults(
        normalized,
        errors=errors,
        query_id=str(payload.get("queryId") or ""),
        total_page_num=_as_int(payload.get("totalPageNum")) or 1,
    )
