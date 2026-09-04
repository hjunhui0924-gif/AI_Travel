from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


TravelIntent = Literal[
    "rail_query",
    "flight_query",
    "transport_compare",
    "trip_plan",
    "nearby_explore",
    "trip_replan",
]


@dataclass(slots=True)
class TravelQuery:
    raw_text: str
    intent: TravelIntent
    origin: str = ""
    destination: str = ""
    city: str = ""
    date: str = ""
    start_date: str = ""
    end_date: str = ""
    days: int = 1
    travelers: int = 1
    budget: str = ""
    travel_mode: str = ""
    preferences: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    locked_item_ids: list[str] = field(default_factory=list)
    date_is_assumed: bool = True
    duration_is_assumed: bool = True
    duration_was_capped: bool = False
    attachment_notes: list[str] = field(default_factory=list)
    named_places: list[str] = field(default_factory=list)
    # A destination can be a single city or a broader province/region.  Keep
    # the scope explicit so a province query can be clarified before city-
    # scoped adapters are called.
    destination_scope: str = "unknown"  # unknown | city | province | region
    destination_cities: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransportOption:
    mode: str
    title: str
    depart_time: str = ""
    arrive_time: str = ""
    duration: str = ""
    price: str = ""
    summary: str = ""
    provider: str = ""
    seats: list[str] = field(default_factory=list)
    is_demo: bool = False
    # Dates are separate from clock times so an overnight train/flight is
    # unambiguous to the calendar and to the itinerary renderer.
    depart_date: str = ""
    arrive_date: str = ""
    # Provider-reported available seats for a selected fare, when supplied.
    # ``None`` means the provider did not expose a reliable seat count.
    seat_count: int | None = None
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransportPage:
    """Pagination metadata for provider-backed transport candidates."""

    mode: str
    offset: int = 0
    limit: int = 5
    returned_count: int = 0
    total_count: int = 0
    has_more: bool = False
    filter: str = "all"


@dataclass(slots=True)
class TransportQueryPage:
    """A page of provider-backed transport options for an explicit query."""

    options: list[TransportOption] = field(default_factory=list)
    pages: list[TransportPage] = field(default_factory=list)
    sources: list[Evidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TimelineItem:
    time_label: str
    title: str
    detail: str = ""
    date: str = ""
    item_id: str = ""
    item_type: str = "activity"
    end_date: str = ""


@dataclass(slots=True)
class PoiRecommendation:
    name: str
    category: str
    area: str = ""
    address: str = ""
    summary: str = ""
    distance: str = ""
    rating: str = ""
    rating_source: str = ""
    estimated_cost: str = ""
    popularity_signal: str = ""
    popularity_source: str = ""
    opening_status: str = "unknown"
    source_ids: list[str] = field(default_factory=list)
    website_url: str = ""
    freshness: str = "unknown"
    is_placeholder: bool = False


@dataclass(slots=True)
class PoiGroup:
    anchor: str
    items: list[PoiRecommendation] = field(default_factory=list)


@dataclass(slots=True)
class RoutePlan:
    mode: str
    origin: str
    destination: str
    duration: str = ""
    distance: str = ""
    summary: str = ""
    origin_address: str = ""
    destination_address: str = ""
    # AMap returns route geometry as semicolon-separated lng,lat pairs. Keep
    # the normalized points in the domain model so consumers do not need to
    # parse provider payloads or rendered text.
    origin_location: str = ""
    destination_location: str = ""
    polyline: list[list[float]] = field(default_factory=list)


@dataclass(slots=True)
class Evidence:
    """A public source record; never store model private reasoning here."""

    evidence_id: str
    source_type: str
    provider: str
    title: str = ""
    url: str = ""
    snippet: str = ""
    retrieved_at: str = ""
    valid_until: str = ""
    freshness: str = "unknown"
    reliability: str = "unknown"
    supports: list[str] = field(default_factory=list)
    is_demo: bool = False


@dataclass(slots=True)
class TravelFact:
    fact_id: str
    fact_type: str
    label: str
    value: str = ""
    confirmed: bool = False
    source_ids: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(slots=True)
class TravelConstraint:
    constraint_id: str
    kind: str
    label: str
    value: str = ""
    hard: bool = False
    satisfied: bool | None = None
    source: str = "user"


@dataclass(slots=True)
class PlanItem:
    item_id: str
    item_type: str
    title: str
    date: str
    start_time: str = ""
    end_time: str = ""
    location: str = ""
    address: str = ""
    detail: str = ""
    status: str = "suggested"
    locked: bool = False
    source_ids: list[str] = field(default_factory=list)
    estimated_cost: str = ""
    travel_minutes: int | None = None
    confidence: str = "unknown"
    # ``date`` is the start date.  Keep an explicit end date for
    # cross-midnight transport or other multi-day arrangements.
    end_date: str = ""
    is_demo: bool = False
    # Provider-reported available seats for a selected transport fare.  This
    # is informational only; it never means a seat was held or a ticket was
    # issued.
    seat_count: int | None = None


@dataclass(slots=True)
class PlanDay:
    date: str
    day_number: int
    title: str = ""
    summary: str = ""
    items: list[PlanItem] = field(default_factory=list)
    has_conflicts: bool = False


@dataclass(slots=True)
class TravelPlan:
    plan_id: str
    thread_id: str
    version: int
    timezone: str
    start_date: str
    end_date: str
    requested_days: int = 1
    projected_days: int = 0
    calendar_truncated: bool = False
    projection_end_date: str = ""
    origin: str = ""
    destination: str = ""
    travelers: int = 1
    preferences: list[str] = field(default_factory=list)
    summary: str = ""
    days: list[PlanDay] = field(default_factory=list)
    facts: list[TravelFact] = field(default_factory=list)
    constraints: list[TravelConstraint] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    adapter_status: dict[str, str] = field(default_factory=dict)
    sources: list[Evidence] = field(default_factory=list)
    search_enabled: bool = False
    status: str = "draft"
    created_at: str = ""
    updated_at: str = ""
    previous_version: int | None = None
    # Locked/confirmed items whose original date is outside the newly
    # projected calendar must remain addressable instead of disappearing
    # during a replan.  Keep this field at the end for positional backwards
    # compatibility with older TravelPlan constructors.
    out_of_range_items: list[PlanItem] = field(default_factory=list)
    # Keep the provider-backed transport candidates alongside the selected
    # calendar item so HTTP consumers never need to parse rendered text.
    transport_options: list[TransportOption] = field(default_factory=list)
    # AMap route segments used by the route preview in the itinerary panel.
    # Kept at the end for positional backwards compatibility with older plans.
    route_plans: list[RoutePlan] = field(default_factory=list)
    # Preserve the scope and selected city anchors when a province-level plan
    # is revised in a later turn.
    destination_scope: str = "unknown"
    destination_cities: list[str] = field(default_factory=list)
    # Pagination state for the provider-backed transport list. Keep it at the
    # end so older positional TravelPlan constructors remain compatible.
    transport_pages: list[TransportPage] = field(default_factory=list)


@dataclass(slots=True)
class TravelPlanResponse:
    intent: TravelIntent
    summary: str
    transport_options: list[TransportOption] = field(default_factory=list)
    route_plans: list[RoutePlan] = field(default_factory=list)
    timeline: list[TimelineItem] = field(default_factory=list)
    poi_recommendations: list[PoiRecommendation] = field(default_factory=list)
    poi_groups: list[PoiGroup] = field(default_factory=list)
    weather_summary: str = ""
    alerts: list[str] = field(default_factory=list)
    extracted_context: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    adapter_status: dict[str, str] = field(default_factory=dict)
    trip_plan: TravelPlan | None = None
    sources: list[Evidence] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    clarification: "ClarificationRequest | None" = None
    pending_query: dict | None = None
    transport_pages: list[TransportPage] = field(default_factory=list)
    # Model-directed conversational action. The deterministic planner may
    # still be used for provider normalization, but this field controls
    # whether the result is shown as an answer, clarification, or plan.
    decision: str = "plan"
    decision_reason: str = ""
    scope_refusal: bool = False


@dataclass(slots=True)
class ClarificationOption:
    key: str
    label: str
    description: str = ""
    value: str = ""


@dataclass(slots=True)
class ClarificationRequest:
    """A small, user-facing request for missing travel requirements."""

    code: str
    prompt: str
    options: list[ClarificationOption] = field(default_factory=list)
