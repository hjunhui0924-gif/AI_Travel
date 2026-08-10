<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useChatStore } from "../stores/chat";
import MessageItem from "./MessageItem.vue";
import ChatComposer from "./ChatComposer.vue";
import MiniCalendarHeat from "./MiniCalendarHeat.vue";

const chat = useChatStore();
const scrollEl = ref<HTMLElement | null>(null);
const showScrollBottom = ref(false);
let stickToBottom = true;

function onScroll() {
  const el = scrollEl.value;
  if (!el) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  stickToBottom = distance < 80;
  showScrollBottom.value = distance > 240;
}

async function scrollToBottom(force = false) {
  if (!force && !stickToBottom) return;
  await nextTick();
  const el = scrollEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

watch(
  () => chat.messages.length,
  () => scrollToBottom(),
);
watch(
  () => chat.messages[chat.messages.length - 1]?.content,
  () => scrollToBottom(),
);

onMounted(() => scrollToBottom(true));
onBeforeUnmount(() => {});
</script>

<template>
  <div ref="scrollEl" class="chat-scroll" @scroll="onScroll">
    <div v-if="chat.historyLoading" class="chat-empty">
      <span class="spinner"></span>
    </div>
    <div v-else-if="!chat.messages.length" class="chat-empty">
      <svg class="hero-art" viewBox="0 0 560 150" fill="none" aria-hidden="true">
        <circle cx="470" cy="38" r="22" class="art-sun" />
        <path d="M0 130 L110 60 L180 130 Z" class="art-mountain" />
        <path d="M140 130 L260 40 L340 130 Z" class="art-mountain alt" />
        <path d="M300 130 L390 72 L460 130 Z" class="art-mountain" />
        <path d="M40 96 C 160 20, 320 20, 480 70" class="art-flight" />
        <path d="M476 62 l14 6 -12 8 z" class="art-plane" />
        <line x1="0" y1="130" x2="560" y2="130" class="art-ground" />
      </svg>
      <div class="hero-kicker">Lazy Trip Copilot</div>
      <h1>说一句想去哪儿，<br />剩下的交给我。</h1>
      <p>从车票机票、每日行程到附近美食，一次生成有来源、可锁定、可重新规划的旅行计划。</p>
      <div class="hero-pills">
        <span class="hero-pill">12306 高铁</span>
        <span class="hero-pill">VariFlight 航班</span>
        <span class="hero-pill">高德路线与天气</span>
        <span class="hero-pill">附近餐饮与景点</span>
        <span class="hero-pill">可锁定的行程版本</span>
      </div>
      <MiniCalendarHeat />
    </div>
    <div v-else class="chat-inner">
      <MessageItem
        v-for="(msg, i) in chat.messages"
        :key="i"
        :message="msg"
      />
    </div>
  </div>
  <button
    v-if="showScrollBottom"
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
  <ChatComposer />
</template>
