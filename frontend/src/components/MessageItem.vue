<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ChatMessage } from "../stores/chat";
import { useChatStore } from "../stores/chat";
import { useAuthStore } from "../stores/auth";
import { usePlanStore } from "../stores/plan";
import { renderMarkdown, safeImageUrl } from "../utils/markdown";
import SegmentText from "./SegmentText.vue";
import assistantMarkUrl from "../assets/gpt.png";

const props = defineProps<{ message: ChatMessage }>();
const auth = useAuthStore();
const chat = useChatStore();
const plan = usePlanStore();

const showActivities = ref(Boolean(props.message.streaming));

watch(
  () => props.message.streaming,
  (streaming, wasStreaming) => {
    if (streaming) showActivities.value = true;
    else if (wasStreaming) showActivities.value = false;
  },
);

const isUser = computed(() => props.message.role === "user");

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
            <span>{{ showActivities ? "收起执行步骤" : `查看 ${message.activities.length} 个执行步骤` }}</span>
            <svg class="activity-toggle-chevron" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m7 10 5 5 5-5" />
            </svg>
          </button>
          <Transition name="activity-reveal">
            <div v-if="showActivities" class="activity-feed">
              <div
                v-for="(act, i) in message.activities"
                :key="i"
                class="activity-item"
                :class="{ running: message.streaming && i === message.activities.length - 1 }"
              >
                <span class="activity-state">[{{ act.state || "..." }}]</span>
                <span class="activity-title">{{ act.title || act.stage }}</span>
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
