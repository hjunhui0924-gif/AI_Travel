"""Live health checks for optional travel data providers.

The checks are deliberately explicit and are not called from normal chat
requests.  Run ``python -m services.integration_health --live`` when setting
up a machine or after changing provider credentials.  Output contains status
and diagnostics only; secrets and response bodies are never included.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from datetime import datetime as DateTime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from adapters.amap_adapter import plan_route, search_pois, search_pois_around_location
from adapters.ctrip_flight_adapter import probe_ctrip_flight_page
from adapters.flight_mcp_adapter import is_flight_mcp_enabled, search_flights
from adapters.rail_12306_adapter import query_left_tickets
from services.travel_search import _default_searcher
from utils.weather_utils import geocode_location, get_amap_weather, has_amap_key


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CN_TZ = ZoneInfo("Asia/Shanghai")
LIVE_OK_STATUSES = {"success", "not_configured", "not_requested"}
PROVIDERS = ("amap", "rail_12306", "tavily", "ctrip_h5", "flight_mcp")


def _now_label() -> str:
    return DateTime.now(CN_TZ).isoformat(timespec="seconds")


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    for name in (
        "AMAP_WEB_API_KEY",
        "TAVILY_API_KEY",
        "FLIGHT_MCP_HTTP_URL",
        "FLIGHT_MCP_COMMAND",
    ):
        secret = os.getenv(name, "")
        if secret:
            message = message.replace(secret, "<redacted>")
    message = re.sub(r"(?i)(key|token|secret)=([^&\s]+)", r"\1=<redacted>", message)
    return f"{type(exc).__name__}: {message[:500]}"


@dataclass(slots=True)
class IntegrationCheck:
    provider: str
    status: str
    configured: bool
    message: str = ""
    latency_ms: int = 0
    checked_at: str = field(default_factory=_now_label)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "configured": self.configured,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
            "details": self.details,
        }


def _run(provider: str, configured: bool, fn: Callable[[], tuple[str, str, dict[str, Any]]]) -> IntegrationCheck:
    started = time.monotonic()
    try:
        status, message, details = fn()
    except Exception as exc:
        return IntegrationCheck(
            provider=provider,
            status="failed",
            configured=configured,
            message=_safe_error(exc),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
    return IntegrationCheck(
        provider=provider,
        status=status,
        configured=configured,
        message=message,
        latency_ms=round((time.monotonic() - started) * 1000),
        details=details,
    )


def _subcheck(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        value = fn()
        if isinstance(value, list):
            details: dict[str, Any] = {"count": len(value)}
        elif isinstance(value, dict):
            details = {"keys": sorted(str(key) for key in value.keys())[:20]}
            for key in ("adcode", "location", "reporttime"):
                if key in value:
                    details[key] = value[key]
        else:
            details = {"type": type(value).__name__}
        return {
            "name": name,
            "status": "success",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "details": details,
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "failed",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "message": _safe_error(exc),
        }


def _aggregate_subchecks(subchecks: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    successes = sum(item.get("status") == "success" for item in subchecks)
    failures = [item for item in subchecks if item.get("status") == "failed"]
    if not failures:
        status = "success"
        message = "所有高德子检查通过。"
    elif successes:
        status = "partial"
        message = "部分高德子检查通过，失败项已列在 details.subchecks。"
    else:
        status = "failed"
        message = "高德子检查全部失败。"
    return status, message, {"subchecks": subchecks}


def _check_amap() -> tuple[str, str, dict[str, Any]]:
    if not has_amap_key():
        return "not_configured", "未配置 AMAP_WEB_API_KEY。", {}

    anchor = "杭州市西湖区灵隐路1号"
    subchecks = [_subcheck("geocode", lambda: geocode_location(anchor))]
    subchecks.append(_subcheck("weather_forecast", lambda: get_amap_weather("杭州市", forecast=True)))
    subchecks.append(_subcheck("poi_text", lambda: search_pois("杭州市西湖区", "餐厅", page_size=3)))
    subchecks.append(
        _subcheck(
            "poi_around",
            lambda: search_pois_around_location("120.14827,30.23737", "餐厅", page_size=3),
        )
    )
    for mode in ("walking", "driving", "transit"):
        subchecks.append(
            _subcheck(
                f"route_{mode}",
                lambda mode=mode: plan_route("杭州东站", anchor, strategy=mode),
            )
        )
    return _aggregate_subchecks(subchecks)


def _rail_query_parameters() -> tuple[str, str, str]:
    query_date = os.getenv("RAIL_HEALTH_DATE", "").strip() or (
        DateTime.now(CN_TZ).date() + timedelta(days=7)
    ).isoformat()
    origin = os.getenv("RAIL_HEALTH_ORIGIN", "广州南").strip()
    destination = os.getenv("RAIL_HEALTH_DESTINATION", "深圳北").strip()
    return query_date, origin, destination


def _check_rail() -> tuple[str, str, dict[str, Any]]:
    query_date, origin, destination = _rail_query_parameters()
    options = query_left_tickets(query_date, origin, destination)
    status = "success" if options else "empty"
    message = f"12306 查询返回 {len(options)} 条原始车次结果。" if options else "12306 请求成功但没有车次结果。"
    return status, message, {
        "date": query_date,
        "origin": origin,
        "destination": destination,
        "count": len(options),
    }


def _check_tavily() -> tuple[str, str, dict[str, Any]]:
    if not os.getenv("TAVILY_API_KEY"):
        return "not_configured", "未配置 TAVILY_API_KEY。", {}
    searcher = _default_searcher()
    if searcher is None:
        return "not_configured", "Tavily 依赖不可用或未配置 API Key。", {}
    payload = searcher.invoke({"query": "杭州西湖附近餐厅"})
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return "failed", "Tavily 返回格式无效。", {}
    results = payload["results"]
    domains = []
    for item in results[:5]:
        if isinstance(item, dict):
            url = str(item.get("url") or "")
            if url:
                domains.append(url.split("/", 3)[2] if "://" in url else url[:80])
    status = "success" if results else "empty"
    return status, f"Tavily 返回 {len(results)} 条发现结果。", {"count": len(results), "domains": domains}


def _flight_query_parameters() -> tuple[str, str, str]:
    query_date = os.getenv("FLIGHT_HEALTH_DATE", "").strip() or (
        DateTime.now(CN_TZ).date() + timedelta(days=7)
    ).isoformat()
    origin = os.getenv("FLIGHT_HEALTH_ORIGIN", "SHA").strip()
    destination = os.getenv("FLIGHT_HEALTH_DESTINATION", "HGH").strip()
    return query_date, origin, destination


def _check_ctrip() -> tuple[str, str, dict[str, Any]]:
    query_date, origin, destination = _flight_query_parameters()
    probe = probe_ctrip_flight_page(origin, destination, query_date)
    if probe.ok:
        status = "success"
        message = probe.message
    elif probe.blocked:
        status = "blocked"
        message = probe.message or "Ctrip H5 请求被风控拦截。"
    elif probe.failure_kind == "empty":
        status = "empty"
        message = probe.message
    else:
        status = "failed"
        message = probe.message or "Ctrip H5 探测失败。"
    return status, message, {
        "date": query_date,
        "origin": origin,
        "destination": destination,
        "http_status": probe.http_status,
        "failure_kind": probe.failure_kind,
        "url": probe.url,
    }


def _check_flight_mcp() -> tuple[str, str, dict[str, Any]]:
    if not is_flight_mcp_enabled():
        return "not_configured", "未显式启用 Flight MCP，应用不会执行真实航班查询。", {}
    query_date, origin, destination = _flight_query_parameters()
    options = search_flights(origin, destination, query_date)
    status = "success" if options else "empty"
    message = f"Flight MCP 返回 {len(options)} 条航班结果。" if options else "Flight MCP 请求完成但没有航班结果。"
    return status, message, {
        "date": query_date,
        "origin": origin,
        "destination": destination,
        "count": len(options),
    }


def run_integration_health_checks(
    *, live: bool = False, only: set[str] | None = None
) -> list[IntegrationCheck]:
    selected = set(only or PROVIDERS)
    checks: list[IntegrationCheck] = []
    if "amap" in selected:
        checks.append(
            _run(
                "amap",
                has_amap_key(),
                _check_amap if live else lambda: ("not_requested", "未执行实时高德检查。", {}),
            )
        )
    if "rail_12306" in selected:
        checks.append(
            _run(
                "rail_12306",
                True,
                _check_rail if live else lambda: ("not_requested", "未执行实时 12306 检查。", {}),
            )
        )
    if "tavily" in selected:
        checks.append(
            _run(
                "tavily",
                bool(os.getenv("TAVILY_API_KEY")),
                _check_tavily if live else lambda: ("not_requested", "未执行实时 Tavily 检查。", {}),
            )
        )
    if "ctrip_h5" in selected:
        checks.append(
            _run(
                "ctrip_h5",
                True,
                _check_ctrip if live else lambda: ("not_requested", "未执行实时携程 H5 检查。", {}),
            )
        )
    if "flight_mcp" in selected:
        checks.append(
            _run(
                "flight_mcp",
                is_flight_mcp_enabled(),
                _check_flight_mcp if live else lambda: ("not_requested", "未执行实时 Flight MCP 检查。", {}),
            )
        )
    return checks


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查旅行规划外部数据 adapter 的配置和实时可用性")
    parser.add_argument("--live", action="store_true", help="执行真实网络请求；默认只检查配置")
    parser.add_argument("--only", choices=PROVIDERS, action="append", help="只检查指定 provider，可重复传入")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    args = _build_parser().parse_args(argv)
    checks = run_integration_health_checks(live=args.live, only=set(args.only or PROVIDERS))
    payload = {
        "live": args.live,
        "checked_at": _now_label(),
        "checks": [check.to_dict() for check in checks],
        "overall_status": (
            "success"
            if all(check.status in LIVE_OK_STATUSES for check in checks)
            else "needs_attention"
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["overall_status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
