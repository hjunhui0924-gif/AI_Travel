<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useAuthStore } from "./stores/auth";
import { useSessionStore } from "./stores/session";
import { useChatStore } from "./stores/chat";
import { usePlanStore } from "./stores/plan";
import AppSidebar from "./components/AppSidebar.vue";
import ChatView from "./components/ChatView.vue";
import PlanPanel from "./components/PlanPanel.vue";

const auth = useAuthStore();
const session = useSessionStore();
const chat = useChatStore();
const plan = usePlanStore();

// Simple local UI state without another store.
const uiState = reactive({ sidebarOpen: window.innerWidth > 860 });
const appReady = ref(false);
let startupThreadId = "";
let startupWatchHandled = false;
let bootstrapReady = false;

const sidebarOpen = computed({
  get: () => uiState.sidebarOpen,
  set: (v: boolean) => (uiState.sidebarOpen = v),
});

onMounted(async () => {
  await auth.fetchMe();
  await session.initialize();
  startupThreadId = session.threadId;
  await Promise.all([chat.loadHistory(), plan.loadPlan()]);
  appReady.value = true;
  bootstrapReady = true;
});

watch(
  () => session.threadId,
  async (id, prev) => {
    if (!id || id === prev) return;
    // The initial thread is loaded explicitly after auth/session bootstrap.
    // Vue may deliver the watcher callback before or after that await, so
    // suppress exactly that one callback to avoid duplicate requests.
    if (!bootstrapReady) return;
    if (!startupWatchHandled && id === startupThreadId) {
      startupWatchHandled = true;
      return;
    }
    startupWatchHandled = true;
    chat.cancelPending();
    plan.reset();
    await Promise.all([chat.loadHistory(), plan.loadPlan()]);
  },
);

function toggleSidebar() {
  uiState.sidebarOpen = !uiState.sidebarOpen;
}
</script>

<template>
  <div class="app-shell">
    <AppSidebar :open="sidebarOpen" @close="sidebarOpen = false" />
    <div
      v-if="sidebarOpen"
      class="mobile-sidebar-backdrop"
      aria-hidden="true"
      @click="sidebarOpen = false"
    ></div>
    <div class="main-column">
      <div class="header">
        <button class="icon-btn" title="显示或隐藏侧边栏" @click="toggleSidebar">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round">
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <div class="header-title">Travel Planning Workspace</div>
        <button
          class="icon-btn"
          :class="{ active: plan.panelOpen }"
          title="行程计划面板"
          @click="plan.panelOpen = !plan.panelOpen"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="4" width="18" height="18" rx="2" />
            <line x1="16" y1="2" x2="16" y2="6" />
            <line x1="8" y1="2" x2="8" y2="6" />
            <line x1="3" y1="10" x2="21" y2="10" />
          </svg>
        </button>
      </div>
      <ChatView v-if="appReady" />
      <div v-else class="app-loading"><span class="spinner"></span></div>
    </div>
    <PlanPanel />
  </div>
</template>
