<script setup lang="ts">
import { ref } from "vue";
import { usePlanStore } from "../stores/plan";
import { useChatStore } from "../stores/chat";

const plan = usePlanStore();
const chat = useChatStore();

const message = ref("");
const searchEnabled = ref(false);

async function submit() {
  const text = message.value.trim();
  if (!text || plan.replanning) return;
  message.value = "";
  const res = await plan.replan(text, searchEnabled.value);
  if (!res) return; // error banner already shown in the panel
  // Surface the replan narrative in the chat stream with real backend data.
  chat.messages.push({
    role: "user",
    content: `【重规划】${text}`,
    search_enabled: searchEnabled.value,
  });
  chat.messages.push({
    role: "assistant",
    content: res.final_text || "已生成新版本。",
    answer_segments: res.answer_segments ?? [],
    sources: res.sources ?? [],
    plan_id: res.plan?.plan_id ?? null,
    plan_version: res.plan?.version ?? null,
  });
}
</script>

<template>
  <div class="replan-box">
    <div class="section-title" style="margin-top: 0">调整计划（重规划）</div>
    <textarea
      v-model="message"
      placeholder="例如：第二天少走路，把晚上的景点换成室内活动"
      :disabled="plan.replanning || Boolean(plan.patchingItemId)"
    ></textarea>
    <div class="replan-row">
      <label>
        <input
          v-model="searchEnabled"
          type="checkbox"
          :disabled="plan.replanning || Boolean(plan.patchingItemId)"
        />
        允许联网搜索
      </label>
      <button
        class="replan-submit"
        type="button"
        :disabled="plan.replanning || Boolean(plan.patchingItemId) || !message.trim()"
        @click="submit"
      >
        {{ plan.replanning ? "重规划中..." : "重新规划" }}
      </button>
    </div>
  </div>
</template>
