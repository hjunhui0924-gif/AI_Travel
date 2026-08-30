<script setup lang="ts">
import { computed, ref } from "vue";
import { useAuthStore } from "../stores/auth";
import { useSessionStore } from "../stores/session";
import { useChatStore } from "../stores/chat";
import { usePlanStore } from "../stores/plan";
import AuthModal from "./AuthModal.vue";
import ConfirmDialog from "./ConfirmDialog.vue";
import travelMarkUrl from "../assets/travel-mark.png";

defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; "new-chat": [] }>();

const auth = useAuthStore();
const session = useSessionStore();
const chat = useChatStore();
const plan = usePlanStore();

const authModalOpen = ref(false);
const authMode = ref<"login" | "register">("login");
const accountMenuOpen = ref(false);
const deleteTarget = ref<string | null>(null);
const deleting = ref(false);
const deleteError = ref("");

const guestThreads = computed(() => {
  // Guest mode: only the current guest thread is addressable.
  return [
    {
      thread_id: session.threadId,
      title: session.currentTitle,
    },
  ];
});

// A guest thread only becomes deletable once it actually holds content;
// a freshly minted current session has nothing on the server to delete.
const guestHasContent = computed(() => {
  if (chat.messages.length > 0) return true;
  return !!session.guestTitles[session.threadId];
});

function openAuth(mode: "login" | "register") {
  authMode.value = mode;
  authModalOpen.value = true;
  accountMenuOpen.value = false;
}

async function onLogout() {
  accountMenuOpen.value = false;
  chat.cancelPending();
  plan.reset();
  await auth.logout();
  await session.onAuthChanged();
}

async function onAuthSuccess() {
  const previousThreadId = session.threadId;
  authModalOpen.value = false;
  chat.cancelPending();
  plan.reset();
  await session.onAuthChanged();
  // A successful guest claim intentionally keeps the original guest_ id, so
  // App.vue's thread watcher does not see a change. Reload the two views here
  // in that case; when the id changes, the watcher owns the refresh and avoids
  // duplicate requests.
  if (session.threadId === previousThreadId) {
    await Promise.all([chat.loadHistory(), plan.loadPlan()]);
  }
}

function onNewChat() {
  emit("close");
  emit("new-chat");
}

function onSelect(threadId: string) {
  if (threadId !== session.threadId) {
    chat.cancelPending();
    plan.reset();
  }
  session.selectThread(threadId);
  emit("close");
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  deleteError.value = "";
  try {
    if (deleteTarget.value === session.threadId) {
      chat.cancelPending();
      plan.reset();
    }
    await session.deleteThread(deleteTarget.value);
  } catch (e) {
    deleteError.value =
      e && typeof e === "object" && "message" in e
        ? String((e as { message?: unknown }).message || "删除失败，请稍后重试")
        : "删除失败，请稍后重试";
  } finally {
    deleting.value = false;
    deleteTarget.value = null;
  }
}
</script>

<template>
  <aside class="sidebar" :class="{ collapsed: !open }">
    <div class="sidebar-inner">
      <div class="brand">
        <img :src="travelMarkUrl" alt="旅途规划" />
        <div>
          <div class="brand-name">旅途规划</div>
          <div class="brand-subtitle">旅行规划工作台</div>
        </div>
      </div>

      <button class="new-chat-btn" type="button" @click="onNewChat">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
        <span>新建对话</span>
      </button>

      <div class="session-list">
        <template v-if="auth.authenticated">
          <button
            v-for="s in session.sessions"
            :key="s.thread_id"
            class="session-item"
            :class="{ active: s.thread_id === session.threadId }"
            type="button"
            @click="onSelect(s.thread_id)"
          >
            <span class="session-title">{{ s.title || "未命名会话" }}</span>
            <span
              class="session-delete"
              title="删除会话"
              @click.stop="deleteTarget = s.thread_id"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round">
                <path d="M3 6h18" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              </svg>
            </span>
          </button>
          <div v-if="!session.sessions.length" class="sidebar-empty">暂无会话</div>
        </template>
        <template v-else>
          <button
            v-for="s in guestThreads"
            :key="s.thread_id"
            class="session-item active"
            type="button"
          >
            <span class="session-title">{{ s.title }}</span>
            <span
              v-if="guestHasContent"
              class="session-delete"
              title="删除会话"
              @click.stop="deleteTarget = s.thread_id"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round">
                <path d="M3 6h18" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              </svg>
            </span>
          </button>
          <div class="sidebar-empty">游客模式：登录后可保存并管理多个会话</div>
        </template>
      </div>
      <div v-if="deleteError" class="sidebar-error" role="alert">{{ deleteError }}</div>

      <div class="account-panel">
        <button
          class="account-card"
          :class="{ 'is-guest': !auth.authenticated }"
          type="button"
          @click="accountMenuOpen = !accountMenuOpen"
        >
          <div class="account-avatar">{{ auth.avatarLabel }}</div>
          <div class="account-meta">
            <div class="account-name">
              {{ auth.user?.display_name || auth.user?.username || "未登录" }}
            </div>
            <div class="account-subtitle">
              {{ auth.authenticated ? "账户会话已隔离保存" : "登录后可隔离聊天记录" }}
            </div>
          </div>
        </button>
        <div v-if="accountMenuOpen" class="account-menu">
          <template v-if="!auth.authenticated">
            <button class="account-btn" type="button" @click="openAuth('login')">登录</button>
            <button class="account-btn ghost" type="button" @click="openAuth('register')">
              注册
            </button>
          </template>
          <button v-else class="account-btn danger" type="button" @click="onLogout">
            退出登录
          </button>
        </div>
      </div>
    </div>

    <AuthModal
      v-if="authModalOpen"
      :mode="authMode"
      @close="authModalOpen = false"
      @success="onAuthSuccess"
    />
    <ConfirmDialog
      v-if="deleteTarget"
      title="删除会话"
      text="将永久删除该会话的聊天历史、旅行计划和版本，且不可恢复。确定继续吗？"
      confirm-text="永久删除"
      :busy="deleting"
      @cancel="deleteTarget = null"
      @confirm="confirmDelete"
    />
  </aside>
</template>
