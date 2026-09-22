"""Provider-backed transport pagination for existing travel plans.

The first plan response intentionally contains a small page of candidates.
This module lets the UI or a follow-up request ask for more results without
asking the language model to invent, reorder, or fill in transport fields.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.schemas import Evidence, TransportPage, TransportQueryPage, TravelPlan, TravelQuery
from services.flight_service import get_flight_options
from services.rail_service import get_rail_options


DEFAULT_TRANSPORT_PAGE_SIZE = 5
MAX_TRANSPORT_PAGE_SIZE = 20
CN_TZ = ZoneInfo("Asia/Shanghai")


def _page_for(plan: TravelPlan, mode: str) -> TransportPage | None:
    return next(
        (page for page in plan.transport_pages if page.mode == mode),
        None,
    )


def _query_for_plan(plan: TravelPlan, mode: str, page: TransportPage | None) -> TravelQuery:
    normalized_mode = "rail" if mode == "rail" else "flight"
    if normalized_mode == "rail":
        raw_text = "高铁" if page and page.filter == "high_speed" else "火车"
        intent = "rail_query"
    else:
        raw_text = "航班"
        intent = "flight_query"
    return TravelQuery(
        raw_text=raw_text,
        intent=intent,
        origin=plan.origin,
        destination=plan.destination,
        city=plan.destination,
        date=plan.start_date,
        start_date=plan.start_date,
        end_date=plan.end_date,
        days=max(1, plan.requested_days),
        travelers=max(1, plan.travelers),
        travel_mode=normalized_mode,
    )


def get_transport_page(
    plan: TravelPlan,
    mode: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_TRANSPORT_PAGE_SIZE,
) -> TransportQueryPage:
    """Fetch one provider page using the plan's original transport filter."""

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"rail", "flight"}:
        raise ValueError("mode 只支持 rail 或 flight。")
    offset = max(0, int(offset))
    limit = min(MAX_TRANSPORT_PAGE_SIZE, max(1, int(limit)))
    existing_page = _page_for(plan, normalized_mode)
    query = _query_for_plan(plan, normalized_mode, existing_page)

    result = (
        get_rail_options(query, offset=offset, limit=limit)
        if normalized_mode == "rail"
        else get_flight_options(query, offset=offset, limit=limit)
    )
    options = list(result)
    sources: list[Evidence] = []
    source_prefix = "transport_rail" if normalized_mode == "rail" else "transport_flight"
    for index, option in enumerate(options, start=offset + 1):
        evidence_id = f"{source_prefix}_{index:03d}"
        option.source_ids = list(dict.fromkeys([*option.source_ids, evidence_id]))
        sources.append(
            Evidence(
                evidence_id=evidence_id,
                source_type="rail_realtime" if normalized_mode == "rail" else "flight_realtime",
                provider=option.provider or ("12306" if normalized_mode == "rail" else "unknown"),
                title=option.title,
                url=(
                    "https://kyfw.12306.cn/"
                    if normalized_mode == "rail"
                    else "https://open.tuniu.com/mcp/docs/apidoc/mcp/flightMCP.html"
                    if (option.provider or "").lower() == "tuniu"
                    else "https://mcp.variflight.com/"
                    if (option.provider or "").lower() == "variflight"
                    else ""
                ),
                snippet=(
                    f"{option.depart_date or ''} {option.depart_time} → "
                    f"{option.arrive_date or ''} {option.arrive_time} {option.duration}"
                ).strip(),
                retrieved_at=datetime.now(CN_TZ).isoformat(timespec="seconds"),
                freshness="current_query",
                reliability="adapter_result",
                supports=["transport_candidate"],
                is_demo=option.is_demo,
            )
        )
    page = TransportPage(
        mode=normalized_mode,
        offset=getattr(result, "offset", offset),
        limit=getattr(result, "limit", limit),
        returned_count=len(options),
        total_count=getattr(result, "total_count", len(options)),
        has_more=bool(getattr(result, "has_more", False)),
        filter=(existing_page.filter if existing_page else "all"),
    )
    return TransportQueryPage(
        options=options,
        pages=[page],
        sources=sources,
        errors=list(getattr(result, "errors", []) or []),
    )
