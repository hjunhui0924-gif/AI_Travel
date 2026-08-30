import { defineStore } from "pinia";
import * as api from "../api";
import type { ThreadInfo } from "../types/api";
import {
  clearGuestThreadId,
  getOrCreateGuestThreadId,
  isGuestThread,
  resetGuestThreadId,
} from "../utils/guest";
import { useAuthStore } from "./auth";

function readGuestTitles(): Record<string, string> {
  try {
    const raw = localStorage.getItem("ai_agent_guest_titles");
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, string] =>
        typeof entry[1] === "string",
      ),
    );
  } catch {
    // A manually edited/corrupted localStorage value must not prevent the app
    // from booting; the guest title is only a convenience cache.
    return {};
  }
}

function writeGuestTitles(titles: Record<string, string>) {
  try {
    localStorage.setItem("ai_agent_guest_titles", JSON.stringify(titles));
  } catch {
    // Storage may be unavailable in private/restricted browser contexts.
  }
}

export const useSessionStore = defineStore("session", {
  state: () => ({
    threadId: "",
    sessions: [] as ThreadInfo[],
    // Local titles for guest threads (account threads get titles server-side).
    guestTitles: readGuestTitles(),
  }),
  getters: {
    isGuest: (state) => isGuestThread(state.threadId),
    currentTitle(): string {
      if (this.isGuest) {
        return this.guestTitles[this.threadId] || "当前会话";
      }
      return (
        this.sessions.find((s) => s.thread_id === this.threadId)?.title ?? "未命名会话"
      );
    },
  },
  actions: {
    /** Resolve the active thread after auth state is known. */
    async initialize() {
      const auth = useAuthStore();
      if (auth.authenticated) {
        await this.refreshSessions();
        if (this.sessions.length) {
          this.threadId = this.sessions[0].thread_id;
        } else {
          await this.createAccountThread();
        }
      } else {
        this.threadId = getOrCreateGuestThreadId();
      }
    },
    async refreshSessions() {
      const auth = useAuthStore();
      if (!auth.authenticated) {
        this.sessions = [];
        return;
      }
      try {
        const res = await api.getSessions();
        this.sessions = res.sessions ?? [];
      } catch {
        this.sessions = [];
      }
    },
    async createAccountThread() {
      const res = await api.createThread();
      this.threadId = res.thread.thread_id;
      await this.refreshSessions();
    },
    /** "New chat" — account thread for logged-in users, fresh guest id otherwise. */
    async startNewChat() {
      const auth = useAuthStore();
      if (auth.authenticated) {
        await this.createAccountThread();
      } else {
        this.threadId = resetGuestThreadId();
      }
    },
    selectThread(threadId: string) {
      this.threadId = threadId;
    },
    rememberGuestTitle(title: string) {
      if (!this.isGuest || !title) return;
      if (!this.guestTitles[this.threadId]) {
        this.guestTitles[this.threadId] = title.slice(0, 32);
        writeGuestTitles(this.guestTitles);
      }
    },
    /** Called after login/logout so the active thread matches the identity. */
    async onAuthChanged() {
      const auth = useAuthStore();
      if (auth.authenticated) {
        // Logged-in users must switch to account threads (handoff §2).
        await this.refreshSessions();
        const claimedThreadId = auth.claimedThreadId;
        if (claimedThreadId) {
          this.threadId = claimedThreadId;
          delete this.guestTitles[claimedThreadId];
          writeGuestTitles(this.guestTitles);
          clearGuestThreadId();
          auth.claimedThreadId = "";
        } else if (this.sessions.length) {
          this.threadId = this.sessions[0].thread_id;
        } else {
          await this.createAccountThread();
        }
      } else {
        this.sessions = [];
        this.threadId = getOrCreateGuestThreadId();
      }
    },
    async deleteThread(threadId: string) {
      await api.deleteHistory(threadId);
      const auth = useAuthStore();
      if (auth.authenticated) {
        await this.refreshSessions();
        if (this.threadId === threadId) {
          if (this.sessions.length) {
            this.threadId = this.sessions[0].thread_id;
          } else {
            await this.createAccountThread();
          }
        }
      } else if (this.threadId === threadId) {
        delete this.guestTitles[threadId];
        writeGuestTitles(this.guestTitles);
        this.threadId = resetGuestThreadId();
      }
    },
  },
});
