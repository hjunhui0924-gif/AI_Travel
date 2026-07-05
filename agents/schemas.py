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
    days: int = 1
    budget: str = ""
    travel_mode: str = ""
    preferences: list[str] = field(default_factory=list)
    attachment_notes: list[str] = field(default_factory=list)
    named_places: list[str] = field(default_factory=list)


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


@dataclass(slots=True)
class TimelineItem:
    time_label: str
    title: str
    detail: str = ""


@dataclass(slots=True)
class PoiRecommendation:
    name: str
    category: str
    area: str = ""
    address: str = ""
    summary: str = ""
    distance: str = ""


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
