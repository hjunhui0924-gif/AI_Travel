import { defineStore } from "pinia";
import * as api from "../api";
import type {
  ActivityEvent,
  AnswerSegment,
  AttachmentInfo,
  DonePayload,
  HistoryMessage,
  SourceInfo,
} from "../types/api";
import { useSessionStore } from "./session";
import { usePlanStore } from "./plan";
import { useAuthStore } from "./auth";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  attachments?: AttachmentInfo[];
  image_urls?: string[];
  activities?: ActivityEvent[];
  sources?: SourceInfo[];
  answer_segments?: AnswerSegment[];
  search_enabled?: boolean;
  plan_id?: string | null;
  plan_version?: number | null;
  /** local-only flags */
  streaming?: boolean;
  failed?: boolean;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".webp", ".gif",
  ".pdf", ".txt", ".md", ".csv", ".docx", ".doc", ".xlsx", ".xls",
]);

export const useChatStore = defineStore("chat", {
  state: () => ({
    messages: [] as ChatMessage[],
    loading: false,
    streaming: false,
    historyLoading: false,
    searchEnabled: false,
    pendingFiles: [] as File[],
    fileError: "",
    /** Live activity feed for the in-flight request. */
    liveActivities: [] as ActivityEvent[],
    /** Sources streamed for the in-flight request. */
    liveSources: [] as SourceInfo[],
    /** Request guards prevent an old thread from mutating the active thread. */
    requestGeneration: 0,
    historyRequestId: 0,
    activeController: null as AbortController | null,
  }),
  actions: {
    async loadHistory() {
      const session = useSessionStore();
      if (!session.threadId) return;
      const threadId = session.threadId;
      const requestId = ++this.historyRequestId;
      this.historyLoading = true;
      this.messages = [];
      try {
        const res = await api.getHistory(threadId);
        if (
          requestId !== this.historyRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.messages = (res.messages ?? []).map((m: HistoryMessage) => ({
          role: m.role,
          content: m.content ?? "",
          attachments: m.attachments ?? [],
          image_urls: m.image_urls ?? [],
          activities: m.activities ?? [],
          sources: m.sources ?? [],
          answer_segments: m.answer_segments ?? [],
          search_enabled: m.search_enabled,
          plan_id: m.plan_id ?? null,
          plan_version: m.plan_version ?? null,
        }));
      } catch {
        if (
          requestId !== this.historyRequestId ||
          session.threadId !== threadId
        ) {
          return;
        }
        this.messages = [];
      } finally {
        if (requestId === this.historyRequestId && session.threadId === threadId) {
          this.historyLoading = false;
        }
      }
    },

    /** Abort the active stream and invalidate all callbacks from that thread. */
    cancelPending() {
      this.requestGeneration += 1;
      this.activeController?.abort();
      this.activeController = null;
      this.loading = false;
      this.streaming = false;
      this.liveActivities = [];
      this.liveSources = [];
    },

    addFiles(files: FileList | File[]) {
      this.fileError = "";
      for (const file of Array.from(files)) {
        const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
        if (!ALLOWED_EXTENSIONS.has(ext)) {
          this.fileError = `不支持的文件类型：${file.name}`;
          continue;
        }
        if (file.size > MAX_FILE_SIZE) {
          this.fileError = `文件超过 10MB：${file.name}`;
          continue;
        }
        if (!this.pendingFiles.some((f) => f.name === file.name && f.size === file.size)) {
          this.pendingFiles.push(file);
        }
      }
    },
    removeFile(index: number) {
      this.pendingFiles.splice(index, 1);
    },

    async send(message: string) {
      const session = useSessionStore();
      const plan = usePlanStore();
      const text = message.trim();
      if (this.loading) return;
      if (!session.threadId) return;
      if (!text && this.pendingFiles.length === 0) return;

      const threadId = session.threadId;
      const requestGeneration = ++this.requestGeneration;
      const controller = new AbortController();
      this.activeController = controller;
      const isCurrentRequest = () => {
        return (
          requestGeneration === this.requestGeneration &&
          session.threadId === threadId
        );
      };

      session.rememberGuestTitle(text);

      this.messages.push({
        role: "user",
        content: text,
        attachments: this.pendingFiles.map((f) => ({
          name: f.name,
          modality: f.type.startsWith("image/") ? "image" : "text",
        })),
        search_enabled: this.searchEnabled,
      });

      const assistantIndex = this.messages.length;
      this.messages.push({
        role: "assistant",
        content: "",
        activities: [],
        sources: [],
        answer_segments: [],
        streaming: true,
      });
      const assistantMessage = () => this.messages[assistantIndex];

      const form = new FormData();
      form.append("message", text);
      form.append("thread_id", threadId);
      form.append("search_enabled", String(this.searchEnabled));
      for (const file of this.pendingFiles) {
        form.append("files", file, file.name);
      }
      this.pendingFiles = [];

      this.loading = true;
      this.streaming = true;
      this.liveActivities = [];
      this.liveSources = [];
      let sawDone = false;

      try {
        await api.streamChat(form, {
          onActivity: (activity) => {
            if (!isCurrentRequest()) return;
            this.liveActivities.push(activity);
            const current = assistantMessage();
            if (current) current.activities = [...this.liveActivities];
          },
          onSource: (source) => {
            if (!isCurrentRequest()) return;
            this.liveSources.push(source);
            const current = assistantMessage();
            if (current) current.sources = [...this.liveSources];
          },
          onText: (delta) => {
            if (!isCurrentRequest()) return;
            const current = assistantMessage();
            if (current) current.content += delta;
          },
          onDone: (payload: DonePayload) => {
            if (!isCurrentRequest()) return;
            sawDone = true;
            const current = assistantMessage();
            if (!current) return;
            current.streaming = false;
            current.failed = payload.ok === false;
            current.content = payload.final_text || current.content;
            current.answer_segments = payload.answer_segments ?? [];
            current.activities = payload.activities ?? current.activities;
            current.sources = payload.sources ?? current.sources;
            if (payload.trip_plan) {
              current.plan_id = payload.trip_plan.plan_id;
              current.plan_version = payload.trip_plan.version;
              plan.applyPlan(payload.trip_plan);
            }
            plan.setMessageSources(payload.sources ?? []);
            this.streaming = false;
            this.loading = false;
          },
          onError: (message) => {
            if (!isCurrentRequest()) return;
            const current = assistantMessage();
            if (current) {
              current.streaming = false;
              current.failed = true;
              current.content =
                current.content || message || "服务异常，请稍后重试。";
            }
            this.streaming = false;
            this.loading = false;
          },
        });
      } catch {
        if (isCurrentRequest()) {
          const current = assistantMessage();
          if (current) {
            current.streaming = false;
            current.failed = true;
            current.content = current.content || "网络异常，请稍后重试。";
          }
        }
      } finally {
        if (isCurrentRequest()) {
          // A stream that ends without done is an incomplete response, not a
          // successful turn. Keep partial text but make the failure explicit.
          const current = assistantMessage();
          if (!sawDone) {
            if (current) {
              current.failed = true;
              current.content = current.content || "本轮回答未完成，请稍后重试。";
            }
          }
          if (current) current.streaming = false;
          this.streaming = false;
          this.loading = false;
          this.activeController = null;
          const auth = useAuthStore();
          if (auth.authenticated) session.refreshSessions();
        }
      }
    },
  },
});
