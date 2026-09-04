<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useChatStore } from "../stores/chat";
import MessageItem from "./MessageItem.vue";
import ChatComposer from "./ChatComposer.vue";
import HomeView from "./HomeView.vue";

const props = withDefaults(
  defineProps<{ workspace?: boolean; transitioning?: boolean }>(),
  { workspace: false, transitioning: false },
);
const emit = defineEmits<{
  "enter-workspace": [prompt: string];
  "background-change": [image: string];
  "scroll-state": [compact: boolean];
}>();

const chat = useChatStore();
const homeScrollEl = ref<HTMLElement | null>(null);
const workspaceScrollEl = ref<HTMLElement | null>(null);
const showScrollBottom = ref(false);
let stickToBottom = true;
let lastScrollState = false;

function activeScrollEl() {
  return props.workspace ? workspaceScrollEl.value : homeScrollEl.value;
}

function syncScrollState() {
  if (props.workspace) {
    // Workspace scrolling belongs to the conversation surface. Keep the
    // product header spatially fixed instead of turning the chat scroll into
    // a second header transition.
    if (lastScrollState) {
      lastScrollState = false;
      emit("scroll-state", false);
    }
    return;
  }
  const el = activeScrollEl();
  if (!el) return;
  const compact = el.scrollTop > 28;
  if (compact === lastScrollState) return;
  lastScrollState = compact;
  emit("scroll-state", compact);
}

function onScroll() {
  const el = activeScrollEl();
  if (!el) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  stickToBottom = distance < 80;
  showScrollBottom.value = distance > 240;
  syncScrollState();
}

async function scrollToBottom(force = false) {
  if (!force && !stickToBottom) return;
  await nextTick();
  const el = activeScrollEl();
  if (el) el.scrollTop = el.scrollHeight;
}

watch(
  () => chat.messages.length,
  () => {
    if (props.workspace && !props.transitioning) scrollToBottom();
  },
);
watch(
  () => chat.messages[chat.messages.length - 1]?.content,
  () => {
    if (props.workspace && !props.transitioning) scrollToBottom();
  },
);

watch(
  () => props.workspace,
  (workspace) => {
    if (!workspace || props.transitioning) return;
    stickToBottom = true;
    void scrollToBottom(true);
  },
);

watch(
  () => props.transitioning,
  (transitioning, wasTransitioning) => {
    if (transitioning || !wasTransitioning || !props.workspace) return;
    // The shared-element curtain keeps the scroll root at the top while the
    // old explore view is leaving. Only snap to the latest message after the
    // new workspace is visible, otherwise the old view jumps to its end card.
    stickToBottom = true;
    void scrollToBottom(true);
  },
);

onMounted(() => {
  if (chat.messages.length) scrollToBottom(true);
  void nextTick(syncScrollState);
});
onBeforeUnmount(() => {});

function submitHomePrompt(prompt: string) {
  emit("enter-workspace", prompt);
}
</script>

<template>
  <div
    ref="homeScrollEl"
    class="chat-scroll"
    :class="{ 'chat-scroll-home': !props.workspace && !chat.historyLoading, 'chat-scroll-workspace': props.workspace }"
    @scroll="onScroll"
  >
    <div v-if="chat.historyLoading" class="chat-empty">
      <span class="spinner"></span>
    </div>
    <HomeView
      v-if="!props.workspace && !props.transitioning"
      key="explore"
      embedded
      :transitioning="props.transitioning"
      @submit-prompt="submitHomePrompt"
      @background-change="emit('background-change', $event)"
    />
    <div v-else-if="props.workspace" key="workspace" class="workspace-chat-surface">
      <div
        ref="workspaceScrollEl"
        class="workspace-chat-scroll-region"
        aria-label="旅行对话"
        @scroll="onScroll"
      >
        <div class="chat-inner">
          <MessageItem
            v-for="(msg, i) in chat.messages"
            :key="i"
            :message="msg"
          />
        </div>
      </div>
    </div>
  </div>
  <button
  v-if="props.workspace && showScrollBottom"
    class="scroll-bottom-btn"
    title="回到底部"
    @click="scrollToBottom(true)"
  >
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 5v14" />
      <path d="M6 13l6 6 6-6" />
    </svg>
  </button>
  <ChatComposer v-if="props.workspace" />
</template>
