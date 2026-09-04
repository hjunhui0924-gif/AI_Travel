// API contract types, aligned with FRONTEND_HANDOFF.md and agents/schemas.py.
// The backend is the single source of truth; never derive plan data from
// Markdown text.

export interface UserInfo {
  id: number;
  username: string;
  display_name: string;
  avatar_label: string;
  created_at: string;
}

export interface AuthMeResponse {
  status: string;
  authenticated: boolean;
  user: UserInfo | null;
}

export interface ThreadInfo {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface AttachmentInfo {
  name: string;
  extension?: string;
  modality?: string;
  image_url?: string | null;
  storage?: string;
  object_key?: string;
}

export interface ActivityEvent {
  stage?: string;
  title?: string;
  detail?: string;
  state?: string;
  timestamp?: string;
}

export interface SourceInfo {
  evidence_id?: string;
  source_type?: string;
  provider?: string;
  title?: string;
  url?: string;
  // SSE source cards use `summary`; TravelPlan Evidence uses `snippet`.
  summary?: string;
  snippet?: string;
  source_date?: string;
  retrieved_at?: string;
  valid_until?: string;
  freshness?: string;
  reliability?: string;
  supports?: string[];
  is_demo?: boolean;
}

export interface AnswerSegment {
  text: string;
  source_ids: string[];
}

export interface ClarificationOption {
  key: string;
  label: string;
  description?: string;
  value?: string;
}

export interface ClarificationRequest {
  code: string;
  prompt: string;
  options: ClarificationOption[];
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  attachments?: AttachmentInfo[];
  image_urls?: string[];
  activities?: ActivityEvent[];
  sources?: SourceInfo[];
  answer_segments?: AnswerSegment[];
  clarification?: ClarificationRequest | null;
  scope_refusal?: boolean;
  search_enabled?: boolean;
  plan_id?: string | null;
  plan_version?: number | null;
}

export interface TransportOption {
  mode: string;
  title: string;
  depart_time: string;
  arrive_time: string;
  duration: string;
  price: string;
  summary?: string;
  provider: string;
  seats: string[];
  is_demo: boolean;
  depart_date: string;
  arrive_date: string;
  seat_count: number | null;
  source_ids: string[];
}

export interface TransportPage {
  mode: "rail" | "flight" | string;
  offset: number;
  limit: number;
  returned_count: number;
  total_count: number;
  has_more: boolean;
  filter?: string;
}

export interface RoutePlan {
  mode: string;
  origin: string;
  destination: string;
  duration: string;
  distance: string;
  summary: string;
  origin_address: string;
  destination_address: string;
  origin_location: string;
  destination_location: string;
  polyline: [number, number][];
}

export interface PlanItem {
  item_id: string;
  item_type: string;
  title: string;
  date: string;
  start_time: string;
  end_time: string;
  location: string;
  address: string;
  detail: string;
  status: string; // suggested | confirmed | booked | skipped | cancelled
  locked: boolean;
  source_ids: string[];
  estimated_cost: string;
  travel_minutes: number | null;
  confidence: string;
  end_date: string;
  is_demo: boolean;
  seat_count: number | null;
}

export interface PlanDay {
  date: string;
  day_number: number;
  title: string;
  summary: string;
  items: PlanItem[];
  has_conflicts: boolean;
}

export interface Evidence {
  evidence_id: string;
  source_type: string;
  provider: string;
  title: string;
  url: string;
  snippet: string;
  retrieved_at: string;
  valid_until: string;
  freshness: string;
  reliability: string;
  supports: string[];
  is_demo: boolean;
}

export interface TravelFact {
  fact_id: string;
  fact_type: string;
  label: string;
  value: string;
  confirmed: boolean;
  source_ids: string[];
  notes: string;
}

export interface TravelConstraint {
  constraint_id: string;
  kind: string;
  label: string;
  value: string;
  hard: boolean;
  satisfied: boolean | null;
  source: string;
}

export interface TravelPlan {
  plan_id: string;
  thread_id: string;
  version: number;
  timezone: string;
  start_date: string;
  end_date: string;
  requested_days: number;
  projected_days: number;
  calendar_truncated: boolean;
  projection_end_date: string;
  origin: string;
  destination: string;
  destination_scope?: string;
  destination_cities?: string[];
  travelers: number;
  preferences: string[];
  summary: string;
  days: PlanDay[];
  facts: TravelFact[];
  constraints: TravelConstraint[];
  conflicts: string[];
  risks: string[];
  alerts: string[];
  diagnostics: string[];
  adapter_status: Record<string, string>;
  sources: Evidence[];
  search_enabled: boolean;
  status: string;
  created_at: string;
  updated_at: string;
  previous_version: number | null;
  out_of_range_items: PlanItem[];
  transport_options: TransportOption[];
  transport_pages?: TransportPage[];
  route_plans?: RoutePlan[];
}

export interface PlanVersionSummary {
  plan_id: string;
  version: number;
  change_summary: string;
  created_at: string;
}

export interface CalendarDayInfo {
  date: string;
  day_number: number;
  title: string;
  summary: string;
  item_count: number;
  has_conflicts: boolean;
}

export interface CalendarResponse {
  status: string;
  plan_id: string | null;
  version: number | null;
  timezone?: string;
  requested_days?: number;
  projected_days?: number;
  calendar_truncated?: boolean;
  projection_end_date?: string;
  out_of_range_item_count?: number;
  days: CalendarDayInfo[];
}

export interface ApiError {
  status: string;
  message: string;
  current_version?: number;
  httpStatus?: number;
}

export interface DonePayload {
  ok: boolean;
  final_text: string;
  answer_segments: AnswerSegment[];
  clarification?: ClarificationRequest | null;
  scope_refusal?: boolean;
  decision?: "answer" | "clarify" | "plan" | "refuse" | string;
  decision_reason?: string;
  activities: ActivityEvent[];
  sources: SourceInfo[];
  trip_plan: TravelPlan | null;
  transport_options?: TransportOption[];
  transport_page?: TransportPage | null;
  attachments: { name: string; modality: string }[];
}
