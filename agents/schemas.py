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

OptimizationObjective = Literal["fastest", "cheapest", "least_walking", "balanced"]
TravelPace = Literal["relaxed", "normal", "packed"]


@dataclass(slots=True)
class ProviderMeta:
    """Public diagnostics for one provider call.

    The value is deliberately separate from the provider payload.  This lets
    the UI explain a partial result without treating an exception string as a
    travel fact, and keeps retry information available for a bounded retry
    action.
    """

    provider: str
    status: str = "not_requested"  # success|empty|failed|partial|not_configured
    retryable: bool = False
    error_code: str = ""
    attempts: int = 0
    latency_ms: int = 0
    retrieved_at: str = ""
    valid_until: str = ""


@dataclass(slots=True)
class PlaceCandidate:
    candidate_id: str
    provider_id: str
    name: str
    category: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    location: str = ""
    distance_from_city_center: str = ""
    opening_status: str = "unknown"
    confidence: str = "unknown"
    source_ids: list[str] = field(default_factory=list)
    query_text: str = ""


@dataclass(slots=True)
class OpeningWindow:
    date: str
    target_id: str = ""
    open_time: str = ""
    close_time: str = ""
    last_entry_time: str | None = None
    closed_reason: str | None = None
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BookingRequirement:
    target_id: str = ""
    required: bool = False
    booking_url: str | None = None
    booking_note: str = ""
    booking_status: str = "unknown"  # unknown|recommended|required|confirmed
    verification: str = "unknown"  # verified|discovered|unknown
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DailyWeather:
    date: str
    day_weather: str = ""
    night_weather: str = ""
    day_temp_c: float | None = None
    night_temp_c: float | None = None
    rain_probability: float | None = None
    wind_level: str = ""
    humidity: str = ""
    source_id: str = ""
    retrieved_at: str = ""


@dataclass(slots=True)
class CareReminder:
    date: str
    message: str
    rule: str = ""
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RouteSegment:
    segment_id: str
    date: str
    origin_place_id: str
    destination_place_id: str
    mode: str
    distance_meters: int | None = None
    duration_minutes: int | None = None
    estimated_cost: str | None = None
    buffer_minutes: int = 20
    walking_minutes: int | None = None
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransportEdge:
    origin_city: str
    destination_city: str
    mode: str
    depart_at: str = ""
    arrive_at: str = ""
    duration_minutes: int | None = None
    price: str | None = None
    transfer_count: int = 0
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScheduleConstraint:
    constraint_id: str
    constraint_type: str
    target_id: str
    hard: bool = False
    start_at: str | None = None
    end_at: str | None = None
    status: str = "unknown"
    source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OptimizationDiagnostic:
    code: str
    message: str
    severity: str = "info"
    details: dict[str, str] = field(default_factory=dict)


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
    objective: OptimizationObjective = "balanced"
    pace: TravelPace = "normal"
    max_daily_walking_minutes: int | None = None
    max_daily_transit_minutes: int | None = None
    must_visit_places: list[str] = field(default_factory=list)
    optional_places: list[str] = field(default_factory=list)
    date_flexibility: bool = False
    arrival_deadline: str = ""
    departure_deadline: str = ""
    meal_preferences: list[str] = field(default_factory=list)
    resolved_place_candidates: list[PlaceCandidate] = field(default_factory=list)


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
    provider_id: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    location: str = ""
    opening_windows: list[OpeningWindow] = field(default_factory=list)
    booking_requirement: BookingRequirement = field(default_factory=BookingRequirement)


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
    # Numeric route facts are kept separate from display strings. Optimizers
    # must never infer cost or duration by scraping summary text.
    duration_minutes: int | None = None
    distance_meters: int | None = None
    estimated_cost: float | None = None
    cost_currency: str = ""
    cost_scope: str = ""


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
    place_id: str = ""
    opening_window_id: str = ""
    booking_requirement_id: str = ""
    buffer_minutes: int = 0
    walking_minutes: int | None = None
    transit_minutes: int | None = None


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
    # MVP extensions.  All fields are append-only so old positional plan
    # constructors and snapshots remain readable.
    route_segments: list[RouteSegment] = field(default_factory=list)
    optimization_objective: str = "balanced"
    optimization_score: float | None = None
    place_candidates: list[PlaceCandidate] = field(default_factory=list)
    opening_windows: list[OpeningWindow] = field(default_factory=list)
    booking_requirements: list[BookingRequirement] = field(default_factory=list)
    daily_weather: list[DailyWeather] = field(default_factory=list)
    care_reminders: list[CareReminder] = field(default_factory=list)
    unresolved_places: list[str] = field(default_factory=list)
    optimization_diagnostics: list[OptimizationDiagnostic] = field(default_factory=list)
    provider_meta: dict[str, ProviderMeta] = field(default_factory=dict)
    schedule_constraints: list[ScheduleConstraint] = field(default_factory=list)
    transport_edges: list[TransportEdge] = field(default_factory=list)


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
    # A transient provider failure can be retried safely with the same user
    # request. This is kept separate from alerts/diagnostics so the frontend
    # can offer a bounded retry action without parsing rendered text.
    retryable: bool = False
    retry_reason: str = ""
    place_candidates: list[PlaceCandidate] = field(default_factory=list)
    unresolved_places: list[str] = field(default_factory=list)
    daily_weather: list[DailyWeather] = field(default_factory=list)
    care_reminders: list[CareReminder] = field(default_factory=list)
    provider_meta: dict[str, ProviderMeta] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)


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
