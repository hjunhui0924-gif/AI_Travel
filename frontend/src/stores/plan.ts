import { defineStore } from "pinia";
import * as api from "../api";
import type { PatchItemPayload } from "../api";
import type {
  ApiError,
  CalendarDayInfo,
  Evidence,
  PlanDay,
  PlanItem,
  PlanVersionSummary,
  SourceInfo,
  TravelPlan,
} from "../types/api";
import { useSessionStore } from "./session";

export type PanelTab = "itinerary" | "sources" | "status";

function dayCacheKey(threadId: string, version: number, date: string): string {
  return `${threadId}::v${version}::${date}`;
}

export const usePlanStore = defineStore("plan", {
  state: () => ({
    plan: null as TravelPlan | null,
    versions: [] as PlanVersionSummary[],
    calendar: [] as CalendarDayInfo[],
    calendarTruncated: false,
    projectionEndDate: "",
    outOfRangeCount: 0,
    dayCache: {} as Record<string, PlanDay>,
    dayLoading: false,
    dayError: "",
    selectedDate: "",
    // The plan is a floating bottom sheet in the workspace. Keep it closed
    // until the user opens the dock so the exploration surface stays clear.
    panelOpen: false,
    activeTab: "itinerary" as PanelTab,
    /** Sources attached to the latest assistant answer (chat-level). */
    messageSources: [] as SourceInfo[],
    /** Source ids highlighted by clicking a sentence citation. */
    highlightedSourceIds: [] as string[],
    loading: false,
    patchingItemId: "",
    replanning: false,
    exportingFormat: "" as "" | "markdown" | "json",
    sharing: false,
    shareUrl: "",
    shareCopied: false,
    error: "",
    conflict409: null as { currentVersion: number; message: string } | null,
    /** Viewing an older version (read-only preview). */
    viewingVersion: null as number | null,
    viewingPlan: null as TravelPlan | null,
    /** Guards asynchronous plan reads/mutations when the active thread changes. */
    requestGeneration: 0,
    loadRequestId: 0,
    mutationRequestId: 0,
  }),
  getters: {
    /** The plan currently displayed (may be a historical version preview). */
    displayPlan: (state): TravelPlan | null => state.viewingPlan ?? state.plan,
    currentVersion: (state): number => state.plan?.version ?? 0,
    selectedDay(state): PlanDay | null {
      const plan = state.viewingPlan ?? state.plan;
      if (!plan) return null;
      const date = state.selectedDate || state.calendar[0]?.date || plan.days[0]?.date || "";
      return (
        state.dayCache[dayCacheKey(plan.thread_id, plan.version, date)] ??
        plan.days.find((d) => d.date === date) ??
        plan.days[0] ??
        null
      );
    },
    /** Plan-level evidences merged with message sources for the sources tab. */
    allSources(state): (SourceInfo | Evidence)[] {
      const map = new Map<string, SourceInfo | Evidence>();
      const plan = state.viewingPlan ?? state.plan;
      for (const s of plan?.sources ?? []) {
        map.set(s.evidence_id || s.url || s.title, s);
      }
      for (const s of state.messageSources) {
        const key = s.evidence_id || s.url || s.title || "";
        if (key && !map.has(key)) map.set(key, s);
      }
      return [...map.values()];
    },
  },
  actions: {
    setMessageSources(sources: SourceInfo[]) {
      this.messageSources = sources ?? [];
    },
    highlightSources(ids: string[]) {
      this.highlightedSourceIds = ids;
      this.activeTab = "sources";
      this.panelOpen = true;
    },
    applyPlan(plan: TravelPlan) {
      this.plan = plan;
      this.viewingPlan = null;
      this.viewingVersion = null;
      this.conflict409 = null;
      this.activeTab = "itinerary";
      // A newly generated plan should reveal its structured result
      // immediately. Otherwise the route map stays inside the hidden plan
      // sheet and users only see the chat background after submitting.
      this.panelOpen = true;
      this.dayCache = {};
      for (const day of plan.days) {
        this.dayCache[dayCacheKey(plan.thread_id, plan.version, day.date)] = day;
      }
      this.dayError = "";
      this.dayLoading = false;
      this.rebuildCalendar(plan);
      if (!this.selectedDate || !plan.days.some((d) => d.date === this.selectedDate)) {
        this.selectedDate = plan.days[0]?.date ?? "";
      }
    },
    rebuildCalendar(plan: TravelPlan) {
      this.calendar = plan.days.map((d) => ({
        date: d.date,
        day_number: d.day_number,
        title: d.title,
        summary: d.summary,
        item_count: d.items.length,
        has_conflicts: d.has_conflicts,
      }));
      this.calendarTruncated = plan.calendar_truncated;
      this.projectionEndDate = plan.projection_end_date;
      this.outOfRangeCount = plan.out_of_range_items?.length ?? 0;
    },
    applyCalendarResponse(response: Awaited<ReturnType<typeof api.getTravelCalendar>>) {
      this.calendar = response.days ?? [];
      this.calendarTruncated = response.calendar_truncated ?? false;
      this.projectionEndDate = response.projection_end_date ?? "";
      this.outOfRangeCount = response.out_of_range_item_count ?? 0;
    },
    async loadPlan() {
      const session = useSessionStore();
      if (!session.threadId) return;
      const threadId = session.threadId;
      const generation = this.requestGeneration;
      const requestId = ++this.loadRequestId;
      const isCurrentRequest = () =>
        generation === this.requestGeneration &&
        requestId === this.loadRequestId &&
        session.threadId === threadId;
      this.loading = true;
      this.error = "";
      try {
        const res = await api.getTravelPlan(threadId, { includeDays: false });
        if (!isCurrentRequest()) return;
        this.versions = res.versions ?? [];
        if (res.plan) {
          this.plan = res.plan;
          // Existing plans should behave like newly generated plans: if a
          // route is available, keep it discoverable after a refresh or
          // session switch instead of leaving it inside a hidden sheet.
          this.panelOpen = true;
          this.viewingPlan = null;
          this.viewingVersion = null;
          this.dayCache = {};
          this.selectedDate = "";
          const calendar = await api.getTravelCalendar(threadId, res.plan.version);
          if (!isCurrentRequest()) return;
          this.applyCalendarResponse(calendar);
          const firstDate = calendar.days?.[0]?.date ?? "";
          this.selectedDate = firstDate;
          if (firstDate) await this.loadDay(firstDate, res.plan.version, requestId, generation);
        } else {
          this.plan = null;
          this.calendar = [];
          this.calendarTruncated = false;
          this.projectionEndDate = "";
          this.outOfRangeCount = 0;
          this.selectedDate = "";
          this.dayCache = {};
          this.dayLoading = false;
          this.dayError = "";
        }
      } catch (e) {
        if (!isCurrentRequest()) return;
        const err = e as ApiError;
        // 404 = no plan yet or no access; not a hard error for the panel.
        if (err.httpStatus !== 404) this.error = err.message;
        this.plan = null;
        this.calendar = [];
        this.calendarTruncated = false;
        this.projectionEndDate = "";
        this.outOfRangeCount = 0;
        this.dayCache = {};
        this.dayLoading = false;
        this.dayError = "";
        this.viewingPlan = null;
        this.viewingVersion = null;
      } finally {
        if (isCurrentRequest()) this.loading = false;
      }
    },
    async loadDay(
      date: string,
      version?: number,
      requestId?: number,
      generation?: number,
    ) {
      const session = useSessionStore();
      const displayed = this.viewingPlan ?? this.plan;
      if (!session.threadId || !displayed || !date) return;
      const threadId = session.threadId;
      const expectedRequestId = requestId ?? this.loadRequestId;
      const expectedGeneration = generation ?? this.requestGeneration;
      const targetVersion = version ?? displayed.version;
      const key = dayCacheKey(threadId, targetVersion, date);
      if (this.dayCache[key]) return;
      this.dayLoading = true;
      this.dayError = "";
      try {
        const response = await api.getTravelDay(threadId, date, targetVersion);
        if (
          expectedGeneration !== this.requestGeneration ||
          expectedRequestId !== this.loadRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.dayCache[key] = response.day;
      } catch (e) {
        if (
          expectedGeneration !== this.requestGeneration ||
          expectedRequestId !== this.loadRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.dayError = (e as ApiError).message;
      } finally {
        if (
          expectedGeneration === this.requestGeneration &&
          expectedRequestId === this.loadRequestId &&
          session.threadId === threadId
        ) {
          this.dayLoading = false;
        }
      }
    },
    async selectDate(date: string) {
      this.selectedDate = date;
      await this.loadDay(date);
    },
    async viewVersion(version: number | null) {
      const session = useSessionStore();
      if (version === null || version === this.plan?.version) {
        const requestId = ++this.loadRequestId;
        this.viewingVersion = null;
        this.viewingPlan = null;
        if (this.plan) {
          this.selectedDate = "";
          const calendar = await api.getTravelCalendar(session.threadId, this.plan.version);
          if (requestId !== this.loadRequestId || !session.threadId) return;
          this.applyCalendarResponse(calendar);
          const firstDate = calendar.days?.[0]?.date ?? "";
          this.selectedDate = firstDate;
          if (firstDate) await this.loadDay(firstDate, this.plan.version, requestId, this.requestGeneration);
        }
        return;
      }
      if (!session.threadId) return;
      const threadId = session.threadId;
      const generation = this.requestGeneration;
      const requestId = ++this.loadRequestId;
      try {
        const res = await api.getTravelVersion(threadId, version, { includeDays: false });
        if (
          generation !== this.requestGeneration ||
          requestId !== this.loadRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.viewingVersion = version;
        this.viewingPlan = res.plan;
        this.dayCache = {};
        this.selectedDate = "";
        const calendar = await api.getTravelCalendar(threadId, version);
        if (
          generation !== this.requestGeneration ||
          requestId !== this.loadRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.applyCalendarResponse(calendar);
        const firstDate = calendar.days?.[0]?.date ?? "";
        this.selectedDate = firstDate;
        if (firstDate) await this.loadDay(firstDate, version, requestId, generation);
      } catch (e) {
        if (
          generation !== this.requestGeneration ||
          requestId !== this.loadRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.error = (e as ApiError).message;
      }
    },
    async patchItem(item: PlanItem, payload: PatchItemPayload) {
      const session = useSessionStore();
      if (!this.plan || this.replanning || this.patchingItemId) return;
      if (!session.threadId) return;
      const threadId = session.threadId;
      const generation = this.requestGeneration;
      const mutationId = ++this.mutationRequestId;
      // A mutation response is newer than any plan read already in flight.
      this.loadRequestId += 1;
      this.patchingItemId = item.item_id;
      this.conflict409 = null;
      try {
        const res = await api.patchPlanItem(threadId, item.item_id, {
          ...payload,
          expected_version: this.plan.version,
        });
        if (
          generation !== this.requestGeneration ||
          mutationId !== this.mutationRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.applyPlan(res.plan);
      } catch (e) {
        if (
          generation !== this.requestGeneration ||
          mutationId !== this.mutationRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        const err = e as ApiError;
        if (err.httpStatus === 409) {
          this.conflict409 = {
            currentVersion: err.current_version ?? this.plan.version,
            message: err.message,
          };
          await this.loadPlan();
        } else {
          this.error = err.message;
        }
      } finally {
        if (
          generation === this.requestGeneration &&
          mutationId === this.mutationRequestId &&
          session.threadId === threadId
        ) {
          this.patchingItemId = "";
        }
      }
    },
    async replan(message: string, searchEnabled: boolean) {
      const session = useSessionStore();
      if (!this.plan || this.replanning || this.patchingItemId) return null;
      if (!session.threadId) return null;
      const threadId = session.threadId;
      const generation = this.requestGeneration;
      const mutationId = ++this.mutationRequestId;
      this.loadRequestId += 1;
      this.replanning = true;
      this.conflict409 = null;
      try {
        const res = await api.replanTravel(threadId, {
          message,
          search_enabled: searchEnabled,
          expected_version: this.plan.version,
        });
        if (
          generation !== this.requestGeneration ||
          mutationId !== this.mutationRequestId ||
          session.threadId !== threadId
        ) {
          return null;
        }
        this.applyPlan(res.plan);
        this.setMessageSources(res.sources ?? []);
        await this.loadPlan(); // refresh version list
        return res;
      } catch (e) {
        if (
          generation !== this.requestGeneration ||
          mutationId !== this.mutationRequestId ||
          session.threadId !== threadId
        ) {
          return null;
        }
        const err = e as ApiError;
        if (err.httpStatus === 409) {
          this.conflict409 = {
            currentVersion: err.current_version ?? this.plan.version,
            message: err.message,
          };
          await this.loadPlan();
        } else {
          this.error = err.message;
        }
        return null;
      } finally {
        if (
          generation === this.requestGeneration &&
          mutationId === this.mutationRequestId &&
          session.threadId === threadId
        ) {
          this.replanning = false;
        }
      }
    },
    async exportPlan(format: "markdown" | "json") {
      const session = useSessionStore();
      const displayed = this.displayPlan;
      if (!session.threadId || !displayed || this.exportingFormat) return;
      this.exportingFormat = format;
      this.error = "";
      try {
        const blob = await api.exportTravelPlan(session.threadId, format, displayed.version);
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = `travel-plan-v${displayed.version}.${format === "json" ? "json" : "md"}`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
      } catch (e) {
        this.error = (e as ApiError).message;
      } finally {
        this.exportingFormat = "";
      }
    },
    async createShare() {
      const session = useSessionStore();
      const displayed = this.displayPlan;
      if (!session.threadId || !displayed || this.sharing) return;
      this.sharing = true;
      this.shareCopied = false;
      this.error = "";
      try {
        const response = await api.createTravelShare(session.threadId, {
          version: displayed.version,
          expires_days: 30,
        });
        const rawUrl = response.share.url ?? response.share.api_url ?? "";
        this.shareUrl = rawUrl.startsWith("/")
          ? `${window.location.origin}${rawUrl}`
          : rawUrl;
        if (this.shareUrl && navigator.clipboard?.writeText) {
          try {
            await navigator.clipboard.writeText(this.shareUrl);
            this.shareCopied = true;
          } catch {
            // The link remains visible for manual copying when clipboard is blocked.
          }
        }
      } catch (e) {
        this.error = (e as ApiError).message;
      } finally {
        this.sharing = false;
      }
    },
    reset() {
      this.requestGeneration += 1;
      this.plan = null;
      this.versions = [];
      this.calendar = [];
      this.calendarTruncated = false;
      this.projectionEndDate = "";
      this.outOfRangeCount = 0;
      this.selectedDate = "";
      this.dayCache = {};
      this.dayLoading = false;
      this.dayError = "";
      this.messageSources = [];
      this.highlightedSourceIds = [];
      this.viewingPlan = null;
      this.viewingVersion = null;
      this.conflict409 = null;
      this.error = "";
      this.loading = false;
      this.patchingItemId = "";
      this.replanning = false;
      this.exportingFormat = "";
      this.sharing = false;
      this.shareUrl = "";
      this.shareCopied = false;
    },
  },
});
