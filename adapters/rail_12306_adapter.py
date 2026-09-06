from __future__ import annotations

import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError, URLError


STATION_NAME_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
LEFT_TICKET_INIT_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
LEFT_TICKET_QUERY_PATHS = ["query", "queryA", "queryZ"]
DEFAULT_RAIL_HTTP_TIMEOUT_SECONDS = 8.0
DEFAULT_RAIL_TOTAL_TIMEOUT_SECONDS = 18.0
STATION_MAP_CACHE_TTL_SECONDS = 24 * 60 * 60
LEFT_TICKET_CACHE_TTL_SECONDS = 20.0
_ticket_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}
_ticket_cache_lock = RLock()
_station_cache_lock = RLock()
_station_cache_path = Path(__file__).resolve().parent.parent / "resources" / "rail_station_map.json"
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
    timeout = _env_float(
        "RAIL_HTTP_TIMEOUT_SECONDS", DEFAULT_RAIL_HTTP_TIMEOUT_SECONDS
    )
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


def _station_map_with_ttl() -> dict[str, str]:
    """Use a persistent daily station cache before hitting 12306."""

    ttl = _env_float(
        "RAIL_STATION_CACHE_TTL_SECONDS",
        STATION_MAP_CACHE_TTL_SECONDS,
        minimum=60.0,
        maximum=7 * 24 * 60 * 60,
    )

    def read_cache(*, allow_stale: bool = False) -> dict[str, str] | None:
        try:
            if not _station_cache_path.exists():
                return None
            age = time.time() - _station_cache_path.stat().st_mtime
            if not allow_stale and age > ttl:
                return None
            payload = json.loads(_station_cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            result = {
                str(key): str(value)
                for key, value in payload.items()
                if str(key).strip() and str(value).strip()
            }
            return result or None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    cached = read_cache()
    if cached:
        return cached

    with _station_cache_lock:
        cached = read_cache()
        if cached:
            return cached
        try:
            # Clear the in-process cache when the persistent entry has expired.
            fetch_station_map.cache_clear()
            fresh = fetch_station_map()
        except RailQueryError:
            stale = read_cache(allow_stale=True)
            if stale:
                return stale
            raise

        try:
            _station_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = _station_cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(fresh, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(_station_cache_path)
        except OSError:
            # Cache persistence is an optimization; a fresh network result is
            # still valid when the runtime directory is read-only.
            pass
        return fresh


def _build_opener(timeout_seconds: float | None = None) -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    timeout = timeout_seconds or _env_float(
        "RAIL_HTTP_TIMEOUT_SECONDS", DEFAULT_RAIL_HTTP_TIMEOUT_SECONDS
    )
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
    request_timeout = _env_float(
        "RAIL_HTTP_TIMEOUT_SECONDS", DEFAULT_RAIL_HTTP_TIMEOUT_SECONDS
    )
    total_timeout = _env_float(
        "RAIL_TOTAL_TIMEOUT_SECONDS",
        DEFAULT_RAIL_TOTAL_TIMEOUT_SECONDS,
        minimum=1.0,
        maximum=180.0,
    )
    cache_key = (date, origin_code, destination_code)
    cache_ttl = _env_float(
        "RAIL_TICKET_CACHE_TTL_SECONDS",
        LEFT_TICKET_CACHE_TTL_SECONDS,
        minimum=0.0,
        maximum=300.0,
    )
    now = time.monotonic()
    with _ticket_cache_lock:
        cached = _ticket_cache.get(cache_key)
        if cached and now - cached[0] <= cache_ttl:
            return cached[1]
    deadline = time.monotonic() + total_timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RailQueryError(f"12306 查询超时（>{total_timeout:g}s）")
    opener = _build_opener(timeout_seconds=min(request_timeout, remaining))

    last_error = None
    # ``query`` is the normal endpoint. The fallbacks are only useful for a
    # provider response that explicitly indicates the endpoint is unavailable;
    # retrying all three after a slow network response adds 20–40 seconds to a
    # request without improving the usual success path.
    try:
        max_retries = max(0, min(2, int(os.getenv("RAIL_MAX_RETRIES", "0"))))
    except ValueError:
        max_retries = 0
    query_paths = list(LEFT_TICKET_QUERY_PATHS[: 1 + max_retries])
    for path_index, path in enumerate(query_paths):
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
            if path_index == 0 and isinstance(exc, HTTPError) and exc.code not in {404, 405, 429, 500, 502, 503, 504}:
                break
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
            with _ticket_cache_lock:
                _ticket_cache[cache_key] = (time.monotonic(), payload)
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
    station_map = _station_map_with_ttl()
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
