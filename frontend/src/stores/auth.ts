import { defineStore } from "pinia";
import * as api from "../api";
import type { UserInfo } from "../types/api";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as UserInfo | null,
    ready: false,
    claimedThreadId: "",
  }),
  getters: {
    authenticated: (state) => state.user !== null,
    avatarLabel: (state) =>
      state.user?.avatar_label ||
      state.user?.display_name?.charAt(0)?.toUpperCase() ||
      "G",
  },
  actions: {
    async fetchMe() {
      try {
        const res = await api.getMe();
        this.user = res.authenticated ? res.user : null;
      } catch {
        this.user = null;
      } finally {
        this.ready = true;
      }
    },
    async login(username: string, password: string, guestThreadId = "", guestTitle = "") {
      const res = await api.login(username, password, guestThreadId, guestTitle);
      if (res.status !== "success" || !res.user) {
        throw new Error(res.message || "登录失败");
      }
      this.user = res.user;
      this.claimedThreadId = res.claimed_thread?.thread_id ?? "";
    },
    async register(
      username: string,
      password: string,
      displayName: string,
      guestThreadId = "",
      guestTitle = "",
    ) {
      const res = await api.register(
        username,
        password,
        displayName,
        guestThreadId,
        guestTitle,
      );
      if (res.status !== "success" || !res.user) {
        throw new Error(res.message || "注册失败");
      }
      this.user = res.user;
      this.claimedThreadId = res.claimed_thread?.thread_id ?? "";
    },
    async logout() {
      try {
        await api.logout();
      } finally {
        this.user = null;
        this.claimedThreadId = "";
      }
    },
  },
});
