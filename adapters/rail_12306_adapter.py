from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.parse
import urllib.request


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


def fetch_station_map() -> dict[str, str]:
    request = urllib.request.Request(STATION_NAME_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()

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


def _build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    opener.open(LEFT_TICKET_INIT_URL, timeout=30).read()
    opener.open(STATION_NAME_URL, timeout=30).read()
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
    opener = _build_opener()

    last_error = None
    for path in LEFT_TICKET_QUERY_PATHS:
        url = f"https://kyfw.12306.cn/otn/leftTicket/{path}?{params}"
        request = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read()
        except Exception as exc:
            last_error = exc
            continue

        text = raw.decode("utf-8-sig", errors="replace").strip()
        if not text.startswith("{"):
            last_error = RailQueryError("12306 returned non-JSON content")
            continue

        payload = json.loads(text)
        if payload.get("httpstatus") == 200 and payload.get("data"):
            return payload

        last_error = RailQueryError(str(payload.get("messages") or "12306 payload missing data"))

    if last_error:
        raise last_error
    raise RailQueryError("12306 query failed without details")


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
