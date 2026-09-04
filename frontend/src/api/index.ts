import type {
  ActivityEvent,
  AnswerSegment,
  AuthMeResponse,
  CalendarResponse,
  DonePayload,
  Evidence,
  HistoryMessage,
  PlanDay,
  PlanItem,
  PlanVersionSummary,
  SourceInfo,
  ThreadInfo,
  TravelPlan,
  TransportOption,
  TransportPage,
  UserInfo,
} from "../types/api";
import {
  apiDelete,
  apiDownload,
  apiGet,
  apiPatchJson,
  apiPostForm,
  apiPostJson,
} from "./http";

// ---------- Auth ----------

export function getMe(): Promise<AuthMeResponse> {
  return apiGet("/auth/me");
}

export interface AuthResponse {
  status: string;
  user?: UserInfo;
  message?: string;
  claimed_thread?: ThreadInfo;
  guest_claim?: { status: string };
}

export function login(username: string, password: string, guestThreadId = "", guestTitle = "") {
  const form = new FormData();
  form.append("username", username);
  form.append("password", password);
  if (guestThreadId) form.append("guest_thread_id", guestThreadId);
  if (guestTitle) form.append("guest_title", guestTitle);
  return apiPostForm<AuthResponse>("/auth/login", form);
}

export function register(
  username: string,
  password: string,
  displayName: string,
  guestThreadId = "",
  guestTitle = "",
) {
  const form = new FormData();
  form.append("username", username);
  form.append("password", password);
  if (displayName) form.append("display_name", displayName);
  if (guestThreadId) form.append("guest_thread_id", guestThreadId);
  if (guestTitle) form.append("guest_title", guestTitle);
  return apiPostForm<AuthResponse>("/auth/register", form);
}

export function logout() {
  return apiPostForm<{ status: string }>("/auth/logout", new FormData());
}

// ---------- Threads / sessions / history ----------

export function createThread() {
  return apiPostJson<{ status: string; thread: ThreadInfo }>("/threads", {});
}

export function getSessions() {
  return apiGet<{ status: string; sessions: ThreadInfo[] }>("/sessions");
}

export function getHistory(threadId: string) {
  return apiGet<{ status: string; messages: HistoryMessage[] }>(
    `/history/${encodeURIComponent(threadId)}`,
  );
}

export function deleteHistory(threadId: string) {
  return apiDelete<{ status: string }>(`/history/${encodeURIComponent(threadId)}`);
}

export function migrateLegacyThreads(threadIds: string[]) {
  return apiPostJson<{
    status: string;
    migrated_thread_ids: string[];
    skipped_thread_ids: string[];
  }>("/threads/migrate-legacy", { thread_ids: threadIds });
}

// ---------- Travel plan ----------

export function getTravelPlan(threadId: string, options: { includeDays?: boolean } = {}) {
  const query = options.includeDays === undefined ? "" : `?include_days=${options.includeDays}`;
  return apiGet<{
    status: string;
    plan: TravelPlan | null;
    versions: PlanVersionSummary[];
  }>(`/travel/plans/${encodeURIComponent(threadId)}${query}`);
}

export function getTravelCalendar(threadId: string, version?: number | null) {
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  return apiGet<CalendarResponse>(
    `/travel/plans/${encodeURIComponent(threadId)}/calendar${query}`,
  );
}

export function getTravelDay(threadId: string, date: string, version?: number | null) {
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  return apiGet<{ status: string; plan_id: string; version: number; day: PlanDay }>(
    `/travel/plans/${encodeURIComponent(threadId)}/days/${encodeURIComponent(date)}${query}`,
  );
}

export function getTravelVersion(
  threadId: string,
  version: number,
  options: { includeDays?: boolean } = {},
) {
  const query = options.includeDays === undefined ? "" : `?include_days=${options.includeDays}`;
  return apiGet<{ status: string; plan: TravelPlan }>(
    `/travel/plans/${encodeURIComponent(threadId)}/versions/${version}${query}`,
  );
}

export function exportTravelPlan(
  threadId: string,
  format: "markdown" | "json",
  version?: number | null,
) {
  const params = new URLSearchParams({ format });
  if (version) params.set("version", String(version));
  return apiDownload(
    `/travel/plans/${encodeURIComponent(threadId)}/export?${params.toString()}`,
  );
}

export function getTravelPlanMap(threadId: string, version?: number | null) {
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  return apiDownload(
    `/travel/plans/${encodeURIComponent(threadId)}/map${query}`,
  );
}

export function getTransportPage(
  threadId: string,
  mode: "rail" | "flight",
  offset: number,
  limit = 5,
  version?: number | null,
) {
  const params = new URLSearchParams({
    mode,
    offset: String(Math.max(0, offset)),
    limit: String(Math.max(1, Math.min(20, limit))),
  });
  if (version) params.set("version", String(version));
  return apiGet<{
    status: string;
    plan_id: string;
    version: number;
    options: TransportOption[];
    page: TransportPage | null;
    sources: Evidence[];
    errors: string[];
  }>(`/travel/plans/${encodeURIComponent(threadId)}/transport?${params.toString()}`);
}

export interface PlanShareInfo {
  share_id: string;
  plan_id: string;
  version: number;
  created_at: string;
  expires_at: string;
  revoked_at?: string | null;
  url?: string;
  api_url?: string;
}

export function createTravelShare(
  threadId: string,
  payload: { version?: number; expires_days?: number } = {},
) {
  return apiPostJson<{ status: string; share: PlanShareInfo }>(
    `/travel/plans/${encodeURIComponent(threadId)}/shares`,
    payload,
  );
}

export function getTravelShares(threadId: string) {
  return apiGet<{ status: string; shares: PlanShareInfo[] }>(
    `/travel/plans/${encodeURIComponent(threadId)}/shares`,
  );
}

export function revokeTravelShare(threadId: string, shareId: string) {
  return apiDelete<{ status: string }>(
    `/travel/plans/${encodeURIComponent(threadId)}/shares/${encodeURIComponent(shareId)}`,
  );
}

export interface PatchItemPayload {
  locked?: boolean;
  status?: string;
  expected_version?: number;
}

export function patchPlanItem(
  threadId: string,
  itemId: string,
  payload: PatchItemPayload,
) {
  return apiPatchJson<{ status: string; plan: TravelPlan; item: PlanItem }>(
    `/travel/plans/${encodeURIComponent(threadId)}/items/${encodeURIComponent(itemId)}`,
    payload,
  );
}

export interface ReplanPayload {
  message: string;
  search_enabled?: boolean;
  expected_version?: number;
}

export function replanTravel(threadId: string, payload: ReplanPayload) {
  return apiPostJson<{
    status: string;
    plan: TravelPlan;
    final_text: string;
    answer_segments: AnswerSegment[];
    sources: SourceInfo[];
  }>(`/travel/plans/${encodeURIComponent(threadId)}/replan`, payload);
}

// ---------- Chat SSE ----------

export interface ChatStreamCallbacks {
  onActivity?: (activity: ActivityEvent) => void;
  onSource?: (source: SourceInfo) => void;
  onText?: (delta: string) => void;
  onDone?: (payload: DonePayload) => void;
  onError?: (message: string) => void;
}

/**
 * POST /chat is a multipart SSE endpoint, so native EventSource cannot be
 * used. Read the body as a stream and split frames on blank lines; network
 * chunks must not be treated as complete events.
 */
export async function streamChat(
  form: FormData,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/chat", {
    method: "POST",
    body: form,
    credentials: "include",
    signal,
  });

  if (!response.ok || !response.body) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = await response.json();
      if (body?.message) message = body.message;
    } catch {
      /* keep default message */
    }
    callbacks.onError?.(message);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const handleFrame = (frame: string) => {
    let eventType = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
    }
    if (!dataLines.length) return;
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }
    switch (eventType) {
      case "activity":
        callbacks.onActivity?.(data as unknown as ActivityEvent);
        break;
      case "source":
        callbacks.onSource?.(data as unknown as SourceInfo);
        break;
      case "text":
        callbacks.onText?.(String(data.delta ?? ""));
        break;
      case "done":
        callbacks.onDone?.(data as unknown as DonePayload);
        break;
      case "error":
        callbacks.onError?.(String(data.message ?? "未知错误"));
        break;
      default:
        break;
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    // SSE frames are separated by a blank line. Handle both LF and CRLF.
    while ((idx = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const frame = buffer.slice(0, idx);
      const match = buffer.slice(idx).match(/^\r?\n\r?\n/);
      buffer = buffer.slice(idx + (match ? match[0].length : 2));
      if (frame.trim()) handleFrame(frame);
    }
  }
  // Flush a potentially incomplete UTF-8 sequence held by TextDecoder at the
  // end of the stream before parsing the final SSE frame.
  buffer += decoder.decode();
  const tail = buffer.trim();
  if (tail) handleFrame(tail);
}
