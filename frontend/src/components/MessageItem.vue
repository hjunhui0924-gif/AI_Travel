<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { ChatMessage } from "../stores/chat";
import { MAX_RETRY_ATTEMPTS, useChatStore } from "../stores/chat";
import { useAuthStore } from "../stores/auth";
import { usePlanStore } from "../stores/plan";
import { renderMarkdown, safeImageUrl } from "../utils/markdown";
import SegmentText from "./SegmentText.vue";
import assistantMarkUrl from "../assets/gpt.png";

const props = defineProps<{ message: ChatMessage }>();
const auth = useAuthStore();
const chat = useChatStore();
const plan = usePlanStore();

const showActivities = ref(Boolean(props.message.streaming || props.message.activities?.length));
const activityFeedEl = ref<HTMLElement | null>(null);
const followActivity = ref(true);

// Activities are appended by the SSE stream. A row appears only when the
// backend has emitted that activity event; there is no playback timer here.
const visibleActivities = computed(() => props.message.activities ?? []);

watch(
  () => props.message.streaming,
  (streaming, wasStreaming) => {
    if (streaming) {
      showActivities.value = true;
      return;
    }
    if (wasStreaming && props.message.interrupted) showActivities.value = true;
  },
);

watch(
  () => props.message.activities?.length ?? 0,
  async () => {
    if (!props.message.streaming || !followActivity.value) return;
    await nextTick();
    const feed = activityFeedEl.value;
    if (feed) feed.scrollTop = feed.scrollHeight;
  },
);

function onActivityFeedScroll(event: Event) {
  const feed = event.currentTarget as HTMLElement;
  followActivity.value = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
}

const isUser = computed(() => props.message.role === "user");

function activityStateLabel(
  state?: string,
  streaming = false,
  interrupted = false,
  failed = false,
) {
  if (state === "running") {
    if (interrupted) return "已停止";
    if (failed) return "未完成";
    return streaming ? "进行中" : "已完成";
  }
  if (state === "failed") return "未完成";
  if (state === "cancelled") return "已停止";
  return "已完成";
}

function activityStageLabel(stage?: string, origin?: string) {
  if (origin === "model" || stage === "progress") return "工作进展";
  if (stage === "reasoning" || stage === "decision" || stage === "think" || stage === "status") return "状态";
  if (stage === "search") return "搜索";
  if (stage === "result") return "结果";
  if (stage === "storage") return "保存";
  return "工具";
}

const renderedContent = computed(() => renderMarkdown(props.message.content));

/**
 * answer_segments is needed for inline citation badges. When a response has
 * no citations, render the complete Markdown document so headings and lists
 * are not shown as raw ``##``/``-`` markers. Never render both paths.
 */
const useSegments = computed(
  () =>
    !isUser.value &&
    !props.message.streaming &&
    (props.message.answer_segments?.some((segment) => (segment.source_ids?.length ?? 0) > 0) ?? false),
);

const textAttachments = computed(() =>
  (props.message.attachments ?? []).filter((a) => a.modality !== "image"),
);
const imageAttachments = computed(() => {
  const fromAttachments = (props.message.attachments ?? [])
    .filter((a) => a.modality === "image" && a.image_url)
    .map((a) => safeImageUrl(a.image_url));
  return [
    ...fromAttachments,
    ...(props.message.image_urls ?? []).map((url) => safeImageUrl(url)),
  ].filter((url): url is string => Boolean(url));
});

const customClarification = ref("");

function chooseClarification(value: string) {
  const prompt = value.trim();
  if (!prompt || chat.loading) return;
  void chat.send(prompt);
}

function submitCustomClarification() {
  const cities = customClarification.value.trim();
  if (!cities || chat.loading) return;
  customClarification.value = "";
  chooseClarification(`我想去${cities}`);
}

function openSources() {
  plan.setMessageSources(props.message.sources ?? []);
  plan.activeTab = "sources";
  plan.openPanel();
}

function openPlan() {
  plan.activeTab = "itinerary";
  plan.openPanel();
  plan.loadPlan();
}

function toggleActivities() {
  showActivities.value = !showActivities.value;
}

function retry() {
  chat.retryMessage(props.message);
}
</script>

<template>
  <div class="message" :class="{ user: isUser, assistant: !isUser }">
    <div class="message-avatar">
      <template v-if="isUser">{{ auth.avatarLabel }}</template>
      <img v-else :src="assistantMarkUrl" alt="旅行助手" />
    </div>
    <div class="message-body">
      <template v-if="!isUser && message.activities?.length">
        <div class="activity-section">
          <button
            class="activity-toggle"
            type="button"
            :aria-expanded="showActivities"
            @click="toggleActivities"
          >
            <span class="activity-toggle-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
                <circle cx="12" cy="12" r="4" />
              </svg>
            </span>
            <span>{{ showActivities ? "收起工作进展" : `查看 ${message.activities.length} 条工作进展` }}</span>
            <svg class="activity-toggle-chevron" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m7 10 5 5 5-5" />
            </svg>
          </button>
          <Transition name="activity-reveal">
            <div v-if="showActivities" class="activity-reveal-outer">
              <div
                ref="activityFeedEl"
                class="activity-feed"
                aria-label="Agent 工作进展"
                aria-live="polite"
                @scroll="onActivityFeedScroll"
              >
                <p class="activity-disclaimer">
                  这里展示 Agent 的公开工作进展和已完成的操作，不展示隐藏思维链。
                </p>
                <div
                  v-for="(act, i) in visibleActivities"
                  :key="`${act.timestamp ?? i}-${i}`"
                  class="activity-item"
                  :class="{
                    'model-progress': act.origin === 'model' || act.stage === 'progress',
                    'reveal-current': message.streaming && i === visibleActivities.length - 1,
                    running: message.streaming && act.state === 'running',
                    stopped: message.interrupted && act.state === 'running',
                    failed: act.state === 'failed' || (message.failed && act.state === 'running'),
                  }"
                >
                  <span class="activity-marker" aria-hidden="true"></span>
                  <span
                    v-if="act.origin !== 'model' && act.stage !== 'progress'"
                    class="activity-stage"
                  >{{ activityStageLabel(act.stage, act.origin) }}</span>
                  <span class="activity-copy">
                    <span class="activity-title">{{ act.title || act.stage }}</span>
                    <small v-if="act.detail" class="activity-detail">{{ act.detail }}</small>
                  </span>
                  <span class="activity-state">{{ activityStateLabel(act.state, message.streaming, message.interrupted, message.failed) }}</span>
                </div>
                <p v-if="message.interrupted" class="activity-interrupted">已停止生成，以上内容为已完成的工作摘要。</p>
              </div>
            </div>
          </Transition>
        </div>
      </template>

      <!-- Streaming or plain assistant text -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div
        v-if="!useSegments"
        class="message-content"
        v-html="renderedContent"
      ></div>
      <!-- Sentence-level citations: authoritative structure from the backend -->
      <div v-else class="message-content">
        <SegmentText
          :segments="message.answer_segments ?? []"
          :sources="message.sources ?? []"
        />
      </div>
      <span v-if="message.streaming" class="loading-dots"></span>

      <div v-if="imageAttachments.length" class="message-images">
        <img v-for="(url, i) in imageAttachments" :key="i" :src="url" alt="用户上传图片" />
      </div>
      <div v-if="textAttachments.length" class="attachment-chips">
        <span v-for="(a, i) in textAttachments" :key="i" class="attachment-chip">
          {{ a.name }}
        </span>
      </div>

      <div v-if="message.failed" class="message-failed">
        本轮回答未成功完成，以上内容可能不完整；请调整后重试。
      </div>
      <div v-if="message.interrupted" class="message-interrupted">
        已停止生成，已保留当前收到的内容。
      </div>
      <div v-if="message.retryable && !message.streaming" class="message-retry-row">
        <span class="message-retry-hint">{{ message.retry_reason || "本轮暂时未完成。" }}</span>
        <button
          v-if="(message.retry_attempts ?? 0) < MAX_RETRY_ATTEMPTS"
          class="message-retry-btn"
          type="button"
          :disabled="chat.loading"
          @click="retry"
        >
          重试（{{ (message.retry_attempts ?? 0) + 1 }}/{{ MAX_RETRY_ATTEMPTS }}）
        </button>
        <span v-else class="message-retry-limit">已达到重试次数</span>
      </div>

      <div v-if="!isUser && message.clarification" class="clarification-card">
        <p class="clarification-prompt">{{ message.clarification.prompt }}</p>
        <div v-if="message.clarification.options?.length" class="clarification-options">
          <template v-for="option in message.clarification.options" :key="option.key">
            <button
              v-if="option.value"
              class="clarification-option"
              type="button"
              :disabled="chat.loading"
              @click="chooseClarification(option.value)"
            >
              <span class="clarification-option-key">{{ option.key }}</span>
              <span class="clarification-option-copy">
                <strong>{{ option.label }}</strong>
                <small v-if="option.description">{{ option.description }}</small>
              </span>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13" /><path d="m13 6 6 6-6 6" /></svg>
            </button>
            <div v-else class="clarification-custom">
              <div class="clarification-custom-copy">
                <span class="clarification-option-key">{{ option.key }}</span>
                <span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span>
              </div>
              <form @submit.prevent="submitCustomClarification">
                <input v-model="customClarification" type="text" placeholder="例如：南京、苏州" :disabled="chat.loading" aria-label="输入自定义城市" />
                <button type="submit" :disabled="chat.loading || !customClarification.trim()">继续</button>
              </form>
            </div>
          </template>
        </div>
      </div>

      <div v-if="!isUser && !message.streaming" class="message-meta-row">
        <button
          v-if="message.sources?.length"
          class="meta-chip"
          type="button"
          @click="openSources"
        >
          {{ message.sources.length }} 个来源
        </button>
        <button
          v-if="message.plan_id"
          class="meta-chip"
          type="button"
          @click="openPlan"
        >
          查看行程计划（v{{ message.plan_version }}）
        </button>
      </div>
    </div>
  </div>
</template>
