import { defineStore } from "pinia";
import * as api from "../api";
import type {
  ActivityEvent,
  AnswerSegment,
  AttachmentInfo,
  DonePayload,
  HistoryMessage,
  ClarificationRequest,
  SourceInfo,
  TransportPage,
} from "../types/api";
import { useSessionStore } from "./session";
import { usePlanStore } from "./plan";
import { useAuthStore } from "./auth";

export const MAX_RETRY_ATTEMPTS = 2;
const CHAT_REQUEST_TIMEOUT_MS = 45_000;

export interface ChatMessage {
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
  transport_page?: TransportPage | null;
  /** local-only flags */
  streaming?: boolean;
  failed?: boolean;
  /** The user stopped generation before the server sent a final done event. */
  interrupted?: boolean;
  retryable?: boolean;
  retry_reason?: string;
  retry_attempts?: number;
  /** Local-only files retained for a retry of the latest request. */
  retry_files?: File[];
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
    activeRequestId: "",
    activeThreadId: "",
    activeTimeoutId: null as number | null,
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
          clarification: m.clarification ?? null,
          scope_refusal: m.scope_refusal ?? false,
          retryable: m.retryable ?? false,
          retry_reason: m.retry_reason ?? "",
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
      const requestId = this.activeRequestId;
      const threadId = this.activeThreadId;
      if (requestId && threadId) {
        void api.cancelChat(threadId, requestId).catch(() => undefined);
      }
      this.requestGeneration += 1;
      this.activeController?.abort();
      this.activeController = null;
      if (this.activeTimeoutId !== null) {
        window.clearTimeout(this.activeTimeoutId);
        this.activeTimeoutId = null;
      }
      this.activeRequestId = "";
      this.activeThreadId = "";
      this.loading = false;
      this.streaming = false;
      this.liveActivities = [];
      this.liveSources = [];
    },

    /** Stop the current response while keeping the text already received. */
    stopGeneration() {
      if (!this.loading && !this.streaming) return;
      const current = [...this.messages]
        .reverse()
        .find((message) => message.role === "assistant" && message.streaming);
      if (current) {
        current.streaming = false;
        current.failed = false;
        current.interrupted = true;
      }
      this.cancelPending();
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

    async send(
      message: string,
      options: {
        files?: File[];
        suppressUserMessage?: boolean;
        retryAttempts?: number;
        insertAssistantAt?: number;
        searchEnabled?: boolean;
      } = {},
    ) {
      const session = useSessionStore();
      const plan = usePlanStore();
      const text = message.trim();
      const files = options.files ? [...options.files] : [...this.pendingFiles];
      if (this.loading) return;
      if (!session.threadId) return;
      if (!text && files.length === 0) return;

      const threadId = session.threadId;
      const activeSearchEnabled = options.searchEnabled ?? this.searchEnabled;
      const requestGeneration = ++this.requestGeneration;
      const controller = new AbortController();
      const requestId =
        typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      this.activeController = controller;
      this.activeRequestId = requestId;
      this.activeThreadId = threadId;
      const isCurrentRequest = () => {
        return (
          requestGeneration === this.requestGeneration &&
          session.threadId === threadId
        );
      };

      session.rememberGuestTitle(text);

      if (!options.suppressUserMessage) {
        this.messages.push({
          role: "user",
          content: text,
          attachments: files.map((f) => ({
            name: f.name,
            modality: f.type.startsWith("image/") ? "image" : "text",
          })),
          search_enabled: activeSearchEnabled,
        });
      }

      const assistantMessageData: ChatMessage = {
        role: "assistant",
        content: "",
        activities: [],
        sources: [],
        answer_segments: [],
        streaming: true,
        retry_attempts: options.retryAttempts ?? 0,
        retry_files: files,
        search_enabled: activeSearchEnabled,
      };
      const insertAt = options.insertAssistantAt;
      const assistantIndex =
        typeof insertAt === "number" && insertAt >= 0 && insertAt <= this.messages.length
          ? insertAt
          : this.messages.length;
      this.messages.splice(assistantIndex, 0, assistantMessageData);
      const assistantMessage = () => this.messages[assistantIndex];

      // 流式合帧：高频文本增量按动画帧合并提交，一帧只做一次内容更新与
      // 重新渲染，降低长回答逐 token 全量重渲染/滚动的布局开销（2026-09-06）。
      let pendingText = "";
      let flushQueued = false;
      const flushText = () => {
        if (pendingText) {
          const current = assistantMessage();
          if (current) current.content += pendingText;
          pendingText = "";
        }
        flushQueued = false;
      };
      const queueTextFlush = () => {
        if (flushQueued) return;
        flushQueued = true;
        requestAnimationFrame(flushText);
      };

      const form = new FormData();
      form.append("message", text);
      form.append("thread_id", threadId);
      form.append("search_enabled", String(activeSearchEnabled));
      form.append("request_id", requestId);
      for (const file of files) {
        form.append("files", file, file.name);
      }
      if (!options.files) this.pendingFiles = [];

      this.loading = true;
      this.streaming = true;
      this.liveActivities = [];
      this.liveSources = [];
      let sawDone = false;
      let timedOut = false;
      const timeoutId = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
        void api.cancelChat(threadId, requestId).catch(() => undefined);
      }, CHAT_REQUEST_TIMEOUT_MS);
      this.activeTimeoutId = timeoutId;

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
            pendingText += delta;
            queueTextFlush();
          },
          onDone: (payload: DonePayload) => {
            if (!isCurrentRequest()) return;
            flushText();
            sawDone = true;
            const current = assistantMessage();
            if (!current) return;
            current.streaming = false;
            current.failed = payload.ok === false;
            current.interrupted = false;
            current.retryable = payload.retryable ?? false;
            current.retry_reason = payload.retry_reason ?? "";
            current.content = payload.final_text || current.content;
            current.answer_segments = payload.answer_segments ?? [];
            current.clarification = payload.clarification ?? null;
            current.scope_refusal = payload.scope_refusal ?? false;
            current.transport_page = payload.transport_page ?? null;
            current.activities = payload.activities ?? current.activities;
            current.sources = payload.sources ?? current.sources;
            if (payload.trip_plan) {
              current.plan_id = payload.trip_plan.plan_id;
              current.plan_version = payload.trip_plan.version;
              plan.applyPlan(payload.trip_plan, { markUnread: !plan.panelOpen });
            }
            if (payload.transport_options?.length && payload.transport_page) {
              plan.appendTransportOptions(
                payload.transport_options,
                payload.transport_page,
                payload.sources ?? [],
              );
            }
            plan.setMessageSources(payload.sources ?? []);
            this.streaming = false;
            this.loading = false;
          },
          onError: (message) => {
            if (!isCurrentRequest()) return;
            flushText();
            const current = assistantMessage();
            if (current) {
              current.streaming = false;
              current.failed = true;
              current.interrupted = false;
              current.retryable = true;
              current.retry_reason = message || "服务暂时不可用，可以重试。";
              current.content =
                current.content || message || "服务异常，请稍后重试。";
            }
            this.streaming = false;
            this.loading = false;
          },
        });
      } catch {
        if (isCurrentRequest()) {
          flushText();
          const current = assistantMessage();
          if (current) {
            current.streaming = false;
            current.failed = true;
            current.retryable = true;
            current.retry_reason = timedOut
              ? "本轮请求超时，可以重试。"
              : "网络异常，可以重试。";
            current.content = current.content || current.retry_reason;
          }
        }
      } finally {
        if (isCurrentRequest()) {
          flushText();
          // A stream that ends without done is an incomplete response, not a
          // successful turn. Keep partial text but make the failure explicit.
          const current = assistantMessage();
          if (!sawDone) {
            if (current) {
              current.failed = true;
              current.interrupted = false;
              current.retryable = true;
              current.retry_reason = timedOut
                ? "本轮请求超时，可以重试。"
                : "本轮回答未完成，可以重试。";
              current.content = current.content || current.retry_reason;
            }
          }
          if (current) current.streaming = false;
          this.streaming = false;
          this.loading = false;
          this.activeController = null;
          this.activeRequestId = "";
          this.activeThreadId = "";
          window.clearTimeout(timeoutId);
          if (this.activeTimeoutId === timeoutId) this.activeTimeoutId = null;
          const auth = useAuthStore();
          if (auth.authenticated) session.refreshSessions();
        }
      }
    },

    retryMessage(message: ChatMessage) {
      if (this.loading || !message.retryable) return;
      const index = this.messages.indexOf(message);
      if (index <= 0 || index >= this.messages.length) return;
      const userMessage = this.messages[index - 1];
      if (userMessage.role !== "user") return;
      const attempts = message.retry_attempts ?? 0;
      if (attempts >= MAX_RETRY_ATTEMPTS) return;
      const files = message.retry_files ? [...message.retry_files] : [];
      this.messages.splice(index, 1);
      void this.send(userMessage.content, {
        files,
        suppressUserMessage: true,
        retryAttempts: attempts + 1,
        insertAssistantAt: index,
        searchEnabled: userMessage.search_enabled ?? false,
      });
    },
  },
});
