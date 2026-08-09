from __future__ import annotations

import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from urllib.error import HTTPError, URLError


STATION_NAME_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
LEFT_TICKET_INIT_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
LEFT_TICKET_QUERY_PATHS = ["query", "queryA", "queryZ"]
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": LEFT_TICKET_INIT_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


class RailQueryError(RuntimeError):
    pass


def _env_float(name: str, default: float, *, minimum: float = 1.0, maximum: float = 120.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


@lru_cache(maxsize=1)
def fetch_station_map() -> dict[str, str]:
    request = urllib.request.Request(STATION_NAME_URL, headers={"User-Agent": "Mozilla/5.0"})
    timeout = _env_float("RAIL_HTTP_TIMEOUT_SECONDS", 12.0)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RailQueryError(f"12306 站点字典请求失败: {type(exc).__name__}") from exc

    content = ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            content = raw.decode(encoding)
            if "\u4e0a\u6d77|SHH" in content or "\u5317\u4eac|BJP" in content:
                break
        except UnicodeDecodeError:
            continue
    if not content:
        content = raw.decode("utf-8", errors="replace")
    return dict(re.findall(r"\|([\u4e00-\u9fa5]+)\|([A-Z]+)\|", content))


def _build_opener(timeout_seconds: float | None = None) -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    timeout = timeout_seconds or _env_float("RAIL_HTTP_TIMEOUT_SECONDS", 12.0)
    try:
        with opener.open(LEFT_TICKET_INIT_URL, timeout=timeout) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RailQueryError(f"12306 会话初始化失败: {type(exc).__name__}") from exc
    return opener


def _load_ticket_payload(date: str, origin_code: str, destination_code: str) -> dict:
    params = urllib.parse.urlencode(
        {
            "leftTicketDTO.train_date": date,
            "leftTicketDTO.from_station": origin_code,
            "leftTicketDTO.to_station": destination_code,
            "purpose_codes": "ADULT",
        }
    )
    request_timeout = _env_float("RAIL_HTTP_TIMEOUT_SECONDS", 12.0)
    total_timeout = _env_float("RAIL_TOTAL_TIMEOUT_SECONDS", 45.0, minimum=1.0, maximum=180.0)
    deadline = time.monotonic() + total_timeout
    opener = _build_opener(timeout_seconds=min(request_timeout, max(1.0, deadline - time.monotonic())))

    last_error = None
    for path in LEFT_TICKET_QUERY_PATHS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        url = f"https://kyfw.12306.cn/otn/leftTicket/{path}?{params}"
        request = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        try:
            with opener.open(request, timeout=min(request_timeout, max(1.0, remaining))) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = RailQueryError(f"12306 {path} 请求失败: {type(exc).__name__}")
            continue

        text = raw.decode("utf-8-sig", errors="replace").strip()
        if not text.startswith("{"):
            last_error = RailQueryError("12306 returned non-JSON content")
            continue

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            last_error = RailQueryError("12306 returned invalid JSON")
            continue
        if not isinstance(payload, dict):
            last_error = RailQueryError("12306 returned a non-object payload")
            continue

        http_status = str(payload.get("httpstatus") or "")
        if http_status == "200" and isinstance(payload.get("data"), dict):
            return payload

        last_error = RailQueryError(str(payload.get("messages") or "12306 payload missing data"))

    if last_error:
        if time.monotonic() >= deadline:
            raise RailQueryError(f"12306 查询超时（>{total_timeout:g}s）: {last_error}") from last_error
        raise last_error
    raise RailQueryError(f"12306 查询超时（>{total_timeout:g}s）")


def _decode_result_item(item: str) -> list[str]:
    decoded = urllib.parse.unquote(item)
    return decoded.split("|")


def query_left_tickets(date: str, origin: str, destination: str) -> list[dict]:
    station_map = fetch_station_map()
    origin_code = station_map.get(origin)
    destination_code = station_map.get(destination)
    if not origin_code or not destination_code:
        raise ValueError(f"未找到车站映射: {origin} -> {destination}")

    payload = _load_ticket_payload(date, origin_code, destination_code)

    results = []
    for item in payload.get("data", {}).get("result", []):
        columns = _decode_result_item(item)
        if len(columns) < 33:
            continue
        results.append(
            {
                "train_no": columns[3],
                "from_station": origin,
                "to_station": destination,
                "depart_time": columns[8],
                "arrive_time": columns[9],
                "duration": columns[10],
                "business_seat": columns[32] or "--",
                "first_class": columns[31] or "--",
                "second_class": columns[30] or "--",
                "soft_sleeper": columns[23] or "--",
                "hard_sleeper": columns[28] or "--",
                "hard_seat": columns[29] or "--",
                "no_seat": columns[26] or "--",
            }
        )
    return results
