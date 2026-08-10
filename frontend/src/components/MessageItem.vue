<script setup lang="ts">
import { computed, ref } from "vue";
import type { ChatMessage } from "../stores/chat";
import { useAuthStore } from "../stores/auth";
import { usePlanStore } from "../stores/plan";
import { renderMarkdown, safeImageUrl } from "../utils/markdown";
import SegmentText from "./SegmentText.vue";
import assistantMarkUrl from "../assets/assistant-mark.png";

const props = defineProps<{ message: ChatMessage }>();
const auth = useAuthStore();
const plan = usePlanStore();

const showActivities = ref(false);

const isUser = computed(() => props.message.role === "user");

const renderedContent = computed(() => renderMarkdown(props.message.content));

/**
 * answer_segments is the authoritative sentence-citation structure. Render it
 * when present; otherwise fall back to final content. Never render both.
 */
const useSegments = computed(
  () =>
    !isUser.value &&
    !props.message.streaming &&
    (props.message.answer_segments?.length ?? 0) > 0,
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

function openSources() {
  plan.setMessageSources(props.message.sources ?? []);
  plan.activeTab = "sources";
  plan.panelOpen = true;
}

function openPlan() {
  plan.activeTab = "itinerary";
  plan.panelOpen = true;
  plan.loadPlan();
}
</script>

<template>
  <div class="message" :class="{ user: isUser, assistant: !isUser }">
    <div class="message-avatar">
      <template v-if="isUser">{{ auth.avatarLabel }}</template>
      <img v-else :src="assistantMarkUrl" alt="AI" />
    </div>
    <div class="message-body">
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

      <!-- Activity feed (collapsed by default once finished) -->
      <template v-if="message.activities?.length">
        <div v-if="message.streaming || showActivities" class="activity-feed">
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
        <div v-else class="message-meta-row">
          <button class="meta-chip" type="button" @click="showActivities = true">
            查看 {{ message.activities.length }} 个执行步骤
          </button>
        </div>
      </template>

      <div v-if="message.failed" class="message-failed">
        本轮回答未成功完成，以上内容可能不完整；请调整后重试。
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
