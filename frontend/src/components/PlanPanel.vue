<script setup lang="ts">
import { computed } from "vue";
import { usePlanStore, type PanelTab } from "../stores/plan";
import ItineraryTab from "./ItineraryTab.vue";
import SourcesTab from "./SourcesTab.vue";
import StatusTab from "./StatusTab.vue";

const plan = usePlanStore();

const tabs: { key: PanelTab; label: string }[] = [
  { key: "itinerary", label: "行程" },
  { key: "sources", label: "来源" },
  { key: "status", label: "状态" },
];

const issueCount = computed(() => {
  const p = plan.displayPlan;
  if (!p) return 0;
  return (
    (p.conflicts?.length ?? 0) +
    (p.alerts?.length ?? 0) +
    (p.risks?.length ?? 0) +
    (p.status === "needs_attention" ? 1 : 0)
  );
});

const sourceCount = computed(() => plan.allSources.length);
</script>

<template>
  <aside class="plan-panel" :class="{ collapsed: !plan.panelOpen }">
    <div class="panel-header">
      <div class="panel-title-row">
        <div class="panel-title">行程计划</div>
        <button
          class="icon-btn"
          title="收起面板"
          @click="plan.panelOpen = false"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2.2" stroke-linecap="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="panel-tabs">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="panel-tab"
          :class="{ active: plan.activeTab === t.key }"
          type="button"
          @click="plan.activeTab = t.key"
        >
          {{ t.label }}
          <span
            v-if="t.key === 'status' && issueCount"
            class="tab-badge"
          >{{ issueCount > 99 ? "99+" : issueCount }}</span>
          <span
            v-else-if="t.key === 'sources' && sourceCount"
            class="tab-badge"
            style="background: var(--info)"
          >{{ sourceCount > 99 ? "99+" : sourceCount }}</span>
        </button>
      </div>
    </div>
    <div class="panel-body">
      <ItineraryTab v-if="plan.activeTab === 'itinerary'" />
      <SourcesTab v-else-if="plan.activeTab === 'sources'" />
      <StatusTab v-else />
    </div>
  </aside>
</template>
