<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useAuthStore } from "./stores/auth";
import { useSessionStore } from "./stores/session";
import { useChatStore } from "./stores/chat";
import { usePlanStore } from "./stores/plan";
import AppSidebar from "./components/AppSidebar.vue";
import ChatView from "./components/ChatView.vue";
import PlanPanel from "./components/PlanPanel.vue";
import PlanDock from "./components/PlanDock.vue";
import beihaiPhoto from "./assets/destinations/beihai-park-white-pagoda.jpg";
import travelMarkUrl from "./assets/travel-mark.png";

const auth = useAuthStore();
const session = useSessionStore();
const chat = useChatStore();
const plan = usePlanStore();

// Simple local UI state without another store.
const uiState = reactive({ sidebarOpen: false });
const appReady = ref(false);
const workspaceMode = ref(false);
const workspaceBackgroundImage = ref(beihaiPhoto);
const headerCompact = ref(false);
const workspaceTransitioning = ref(false);
const workspaceCurtainVisible = ref(false);
const transitionPrompt = ref("");
const transitionOrigin = reactive({
  top: "58%",
  right: "24%",
  bottom: "30%",
  left: "24%",
});
const transitionLayerStyle = computed<Record<string, string>>(() => ({
  "--transition-top": transitionOrigin.top,
  "--transition-right": transitionOrigin.right,
  "--transition-bottom": transitionOrigin.bottom,
  "--transition-left": transitionOrigin.left,
}));
let transitionRun = 0;
let startupThreadId = "";
let startupWatchHandled = false;
let bootstrapReady = false;
let bootstrapPromise: Promise<void> | null = null;

const sidebarOpen = computed({
  get: () => uiState.sidebarOpen,
  set: (v: boolean) => (uiState.sidebarOpen = v),
});

/**
 * Guest access is issued as an HttpOnly cookie at the end of a response.
 * Load history first so the following plan request can reuse that capability
 * instead of racing the first response.
 */
async function loadActiveThreadData() {
  if (session.isGuest) {
    await chat.loadHistory();
    await plan.loadPlan();
    return;
  }
  await Promise.all([chat.loadHistory(), plan.loadPlan()]);
}

onMounted(() => {
  void ensureAppReady();
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
    await loadActiveThreadData();
    workspaceMode.value = chat.messages.length > 0;
  },
);

function toggleSidebar() {
  uiState.sidebarOpen = !uiState.sidebarOpen;
}

async function startNewConversation() {
  transitionRun += 1;
  workspaceCurtainVisible.value = false;
  transitionPrompt.value = "";
  workspaceTransitioning.value = false;
  headerCompact.value = false;
  chat.cancelPending();
  chat.messages = [];
  chat.pendingFiles = [];
  chat.fileError = "";
  plan.reset();
  plan.panelOpen = false;
  workspaceMode.value = false;
  uiState.sidebarOpen = false;
  await session.startNewChat();
  await nextTick();
  document.querySelector<HTMLElement>(".chat-scroll")?.scrollTo({ top: 0, behavior: "smooth" });
}

async function ensureAppReady() {
  if (appReady.value) return;
  if (!bootstrapPromise) {
    bootstrapPromise = (async () => {
      await auth.fetchMe();
      await session.initialize();
      startupThreadId = session.threadId;
      await loadActiveThreadData();
      workspaceMode.value = chat.messages.length > 0;
      appReady.value = true;
      bootstrapReady = true;
    })();
  }
  await bootstrapPromise;
}

async function enterWorkspace(prompt: string) {
  if (workspaceTransitioning.value) return;
  const run = ++transitionRun;
  const mainEl = document.querySelector<HTMLElement>(".main-column");
  const promptEl = document.querySelector<HTMLElement>(".home-prompt");
  const mainRect = mainEl?.getBoundingClientRect();
  const promptRect = promptEl?.getBoundingClientRect();
  if (mainRect && promptRect) {
    const top = Math.max(0, promptRect.top - mainRect.top);
    const left = Math.max(0, promptRect.left - mainRect.left);
    const right = Math.max(0, mainRect.right - promptRect.right);
    const bottom = Math.max(0, mainRect.bottom - promptRect.bottom);
    transitionOrigin.top = `${top}px`;
    transitionOrigin.right = `${right}px`;
    transitionOrigin.bottom = `${bottom}px`;
    transitionOrigin.left = `${left}px`;
  }
  transitionPrompt.value = prompt;
  workspaceTransitioning.value = true;
  workspaceCurtainVisible.value = true;
  await nextTick();
  await new Promise<void>((resolve) => window.setTimeout(resolve, 220));
  if (run !== transitionRun) return;
  workspaceMode.value = true;
  await nextTick();
  const scrollEl = document.querySelector<HTMLElement>(".workspace-chat-scroll-region");
  if (scrollEl) scrollEl.scrollTop = 0;
  void chat.send(prompt);
  await new Promise<void>((resolve) => window.setTimeout(resolve, 300));
  if (run !== transitionRun) return;
  workspaceCurtainVisible.value = false;
  await new Promise<void>((resolve) => window.setTimeout(resolve, 180));
  if (run !== transitionRun) return;
  workspaceTransitioning.value = false;
  transitionPrompt.value = "";
  headerCompact.value = false;
}

function onBackgroundChange(image: string) {
  workspaceBackgroundImage.value = image;
}

function onScrollState(compact: boolean) {
  headerCompact.value = compact;
}

async function returnToExplore() {
  await startNewConversation();
}

</script>

<template>
  <div class="app-shell">
    <AppSidebar :open="sidebarOpen" @close="sidebarOpen = false" @new-chat="startNewConversation" />
    <div
      v-if="sidebarOpen"
      class="mobile-sidebar-backdrop"
      aria-hidden="true"
      @click="sidebarOpen = false"
    ></div>
    <div
      class="main-column"
      :class="{ 'is-explore': appReady && !workspaceMode, 'is-workspace': workspaceMode, 'is-transitioning': workspaceTransitioning }"
      :style="{ '--workspace-background-image': `url(${workspaceBackgroundImage})` }"
    >
      <Transition name="app-background">
        <div
          :key="workspaceBackgroundImage"
          class="app-background"
          :style="{ backgroundImage: `url(${workspaceBackgroundImage})` }"
          aria-hidden="true"
        ></div>
      </Transition>
      <div
        class="header"
        :class="{ 'is-explore': appReady && !workspaceMode, 'is-workspace': workspaceMode, 'is-compact': headerCompact }"
      >
        <button class="icon-btn" aria-label="显示或隐藏侧边栏" title="显示或隐藏侧边栏" @click="toggleSidebar">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round">
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <div class="header-brand">
          <img :src="travelMarkUrl" alt="" />
          <span class="header-brand-copy">
            <strong>旅途规划</strong>
            <small>旅行规划工作台</small>
          </span>
        </div>
        <button
          v-if="workspaceMode"
          class="workspace-return-button"
          type="button"
          aria-label="返回探索"
          title="返回探索"
          @click="returnToExplore"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M19 12H5" />
            <path d="m11 18-6-6 6-6" />
          </svg>
          <span>返回探索</span>
        </button>
      </div>
      <ChatView
        v-if="appReady"
        :workspace="workspaceMode"
        :transitioning="workspaceTransitioning"
        @enter-workspace="enterWorkspace"
        @background-change="onBackgroundChange"
        @scroll-state="onScrollState"
      />
      <div v-if="!appReady" class="app-loading"><span class="spinner"></span></div>
      <Transition name="workspace-curtain">
        <div
          v-if="workspaceCurtainVisible"
          class="workspace-transition-layer"
          :style="transitionLayerStyle"
          aria-hidden="true"
        >
          <div class="workspace-transition-surface">
            <svg class="workspace-transition-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="m16 16 4 4" />
            </svg>
            <span class="workspace-transition-query">{{ transitionPrompt }}</span>
            <span class="workspace-transition-status">正在整理你的旅行计划</span>
          </div>
        </div>
      </Transition>
    </div>
    <div
      v-if="plan.panelOpen"
      class="plan-sheet-backdrop"
      aria-hidden="true"
      @click="plan.panelOpen = false"
    ></div>
    <PlanDock
      v-if="appReady && !plan.panelOpen"
      :has-messages="Boolean(chat.messages.length)"
      :has-plan="Boolean(plan.plan)"
      :has-new-plan="plan.planUnread"
      @open="plan.openPanel()"
    />
    <PlanPanel />
  </div>
</template>
