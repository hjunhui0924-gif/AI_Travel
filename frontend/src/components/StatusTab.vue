<script setup lang="ts">
import { computed, ref } from "vue";
import { usePlanStore } from "../stores/plan";

const plan = usePlanStore();
const p = computed(() => plan.displayPlan);
const diagnosticsOpen = ref(false);

const ADAPTER_LABELS: Record<string, string> = {
  rail: "12306 铁路",
  flight: "航班",
  route: "路线",
  poi: "地点",
  weather: "天气",
  web_search: "联网搜索",
};

const STATE_LABELS: Record<string, string> = {
  success: "成功",
  empty: "无结果",
  failed: "失败",
  not_configured: "未配置",
  not_requested: "未请求",
  disabled: "已关闭",
  partial: "部分成功",
  blocked: "被拦截",
};

const adapters = computed(() => {
  const status = p.value?.adapter_status ?? {};
  return Object.entries(status).map(([key, state]) => ({
    key,
    name: ADAPTER_LABELS[key] ?? key,
    state,
    label: STATE_LABELS[state] ?? state,
  }));
});
</script>

<template>
  <div v-if="!p" class="panel-empty">
    <div class="empty-title">暂无状态</div>
    <p>生成旅行计划后，这里会展示各数据源的查询状态与风险提示。</p>
  </div>
  <template v-else>
    <div class="section-title" style="margin-top: 0">数据源状态</div>
    <div class="adapter-grid">
      <div v-for="a in adapters" :key="a.key" class="adapter-chip">
        <span class="adapter-name">{{ a.name }}</span>
        <span class="adapter-state" :class="a.state">{{ a.label }}</span>
      </div>
    </div>

    <template v-if="p.conflicts?.length">
      <div class="section-title">冲突</div>
      <div v-for="(c, i) in p.conflicts" :key="i" class="notice-block conflict">{{ c }}</div>
    </template>

    <template v-if="p.alerts?.length">
      <div class="section-title">提醒</div>
      <div v-for="(a, i) in p.alerts" :key="i" class="notice-block alert">{{ a }}</div>
    </template>

    <template v-if="p.risks?.length">
      <div class="section-title">风险</div>
      <div v-for="(r, i) in p.risks" :key="i" class="notice-block risk">{{ r }}</div>
    </template>

    <template v-if="p.diagnostics?.length">
      <div
        class="section-title"
        style="cursor: pointer"
        @click="diagnosticsOpen = !diagnosticsOpen"
      >
        调试诊断（{{ p.diagnostics.length }}）{{ diagnosticsOpen ? "▾" : "▸" }}
      </div>
      <template v-if="diagnosticsOpen">
        <div v-for="(d, i) in p.diagnostics" :key="i" class="notice-block diagnostic">
          {{ d }}
        </div>
      </template>
    </template>

    <div
      v-if="
        !p.conflicts?.length && !p.alerts?.length && !p.risks?.length &&
        p.status !== 'needs_attention' &&
        adapters.every((a) => ['success', 'not_requested', 'disabled'].includes(a.state))
      "
      class="notice-block alert"
      style="background: var(--accent-soft); color: var(--accent-strong)"
    >
      所有已请求的数据源均正常。
    </div>
  </template>
</template>
