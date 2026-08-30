<script setup lang="ts">
import { computed, ref } from "vue";
import { usePlanStore } from "../stores/plan";
import PlanCalendar from "./PlanCalendar.vue";
import PlanItemCard from "./PlanItemCard.vue";
import TransportCard from "./TransportCard.vue";
import ReplanForm from "./ReplanForm.vue";
import RouteMap from "./RouteMap.vue";

const plan = usePlanStore();

const p = computed(() => plan.displayPlan);
const day = computed(() => plan.selectedDay);
const routePlans = computed(() => p.value?.route_plans ?? []);

const outOfRangeOpen = ref(true);

/**
 * Calendar drawer: auto-expanded when a plan exists, collapsed like a drawer
 * when there is none — but always expandable so the user can browse any
 * month (past dates included). Once the user toggles manually, that choice
 * sticks for the session.
 */
const calendarToggled = ref<boolean | null>(null);
const calendarOpen = computed(() => calendarToggled.value ?? !!p.value);
function toggleCalendar() {
  calendarToggled.value = !calendarOpen.value;
}

async function onVersionChange(e: Event) {
  const value = (e.target as HTMLSelectElement).value;
  await plan.viewVersion(value === "current" ? null : Number(value));
}

const dateRangeText = computed(() => {
  if (!p.value) return "";
  const base = `${p.value.start_date} ~ ${p.value.end_date}`;
  return p.value.calendar_truncated
    ? `${base}（日历投影至 ${p.value.projection_end_date}）`
    : base;
});
</script>

<template>
  <div v-if="plan.loading && !p" class="panel-empty">
    <span class="spinner"></span>
  </div>

  <div v-else-if="!p" class="panel-empty">
    <svg class="empty-illustration" viewBox="0 0 120 80" fill="none" aria-hidden="true">
      <rect x="18" y="14" width="84" height="56" rx="6" class="ill-map" />
      <path d="M18 34c14-8 26 6 40-2s24-10 44 2" class="ill-road" />
      <circle cx="42" cy="30" r="4" class="ill-pin" />
      <circle cx="82" cy="52" r="4" class="ill-pin alt" />
      <path d="M42 30 82 52" class="ill-route" />
    </svg>
    <div class="empty-title">还没有旅行计划</div>
    <p>在聊天里描述你的出行需求，例如「国庆从广州去杭州玩 4 天」。计划生成后，日历会自动展开并高亮行程日期。</p>
  </div>

  <!-- 日历抽屉：有计划自动展开，无计划收起但可点开翻看任意月份 -->
  <div v-if="!plan.loading" class="calendar-drawer">
    <button class="calendar-drawer-toggle" type="button" @click="toggleCalendar">
      <span class="drawer-toggle-left">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
        行程日历
        <span v-if="p" class="drawer-sub">{{ p.start_date }} ~ {{ p.end_date }}</span>
        <span v-else class="drawer-sub">暂无计划，可翻看日期</span>
      </span>
      <svg
        class="drawer-chevron"
        :class="{ open: calendarOpen }"
        width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>
    <PlanCalendar v-if="calendarOpen" />
  </div>

  <template v-if="p">
    <div v-if="plan.error" class="error-banner">{{ plan.error }}</div>
    <div v-if="plan.conflict409" class="conflict-banner">
      计划已被更新（当前 v{{ plan.conflict409.currentVersion }}），你的修改未提交。请基于最新计划重试。
      <button type="button" @click="plan.conflict409 = null">知道了</button>
    </div>
    <div v-if="plan.viewingVersion !== null" class="version-preview-note">
      正在预览历史版本 v{{ plan.viewingVersion }}（只读）。切换到最新版本后才能修改计划项。
    </div>

    <div class="plan-summary">
      <div class="plan-route">
        {{ p.origin || "出发地待定" }} → {{ p.destination || "目的地待定" }}
      </div>
      <div v-if="p.destination_cities?.length" class="plan-route-cities">
        {{ p.destination_scope === "province" ? "城市路线" : "途经" }}：{{ p.destination_cities.join(" · ") }}
      </div>
      <div class="plan-dates">{{ dateRangeText }} · {{ p.travelers }} 人</div>
      <div v-if="p.preferences?.length" class="plan-tags">
        <span v-for="(pref, i) in p.preferences" :key="i" class="plan-tag">{{ pref }}</span>
      </div>
      <div class="plan-version-row">
        <span>版本 v{{ p.version }}</span>
        <template v-if="plan.versions.length > 1">
          <select :value="plan.viewingVersion ?? 'current'" @change="onVersionChange">
            <option value="current">最新（v{{ plan.plan?.version }}）</option>
            <option
              v-for="v in plan.versions.filter((x) => x.version !== plan.plan?.version)"
              :key="v.version"
              :value="v.version"
            >
              v{{ v.version }} · {{ v.change_summary || "历史版本" }}
            </option>
          </select>
        </template>
        <span v-if="p.status === 'needs_attention'" class="badge demo">需关注</span>
      </div>
    </div>

    <RouteMap v-if="routePlans.some((route) => route.polyline?.length >= 2)" :routes="routePlans" />

    <div class="plan-actions" aria-label="计划操作">
      <button
        class="secondary-btn"
        type="button"
        :disabled="!!plan.exportingFormat"
        @click="plan.exportPlan('markdown')"
      >
        {{ plan.exportingFormat === 'markdown' ? '导出中…' : '导出 Markdown' }}
      </button>
      <button
        class="secondary-btn"
        type="button"
        :disabled="!!plan.exportingFormat"
        @click="plan.exportPlan('json')"
      >
        {{ plan.exportingFormat === 'json' ? '导出中…' : '导出 JSON' }}
      </button>
      <button class="secondary-btn" type="button" :disabled="plan.sharing" @click="plan.createShare()">
        {{ plan.sharing ? '生成中…' : '创建分享链接' }}
      </button>
    </div>
    <div v-if="plan.shareUrl" class="share-result">
      <span>{{ plan.shareCopied ? '链接已复制：' : '分享链接：' }}</span>
      <a :href="plan.shareUrl" target="_blank" rel="noopener noreferrer">{{ plan.shareUrl }}</a>
    </div>

    <!-- Conflicts -->
    <template v-if="p.conflicts?.length">
      <div class="section-title">冲突</div>
      <div v-for="(c, i) in p.conflicts" :key="i" class="notice-block conflict">{{ c }}</div>
    </template>

    <!-- Transport options -->
    <template v-if="p.transport_options?.length">
      <div class="section-title">交通候选</div>
      <TransportCard
        v-for="(t, i) in p.transport_options"
        :key="i"
        :option="t"
      />
    </template>

    <!-- Selected day items -->
    <template v-if="day">
      <div class="day-header">
        <span class="day-title">{{ day.title || `第 ${day.day_number} 天` }}</span>
        <span class="day-summary">{{ day.date }} · {{ day.summary }}</span>
      </div>
      <div v-if="!day.items.length" class="panel-empty" style="padding: 16px">
        当天暂无安排
      </div>
      <div v-else class="day-items">
        <PlanItemCard
          v-for="item in day.items"
          :key="item.item_id"
          :item="item"
          :readonly="plan.viewingVersion !== null"
        />
      </div>
    </template>
    <div v-else-if="plan.dayLoading" class="panel-empty" style="padding: 16px">
      <span class="spinner"></span>
    </div>
    <div v-else-if="plan.dayError" class="notice-block risk">
      {{ plan.dayError }}
    </div>

    <!-- Out-of-range locked/confirmed items -->
    <template v-if="p.out_of_range_items?.length">
      <div class="section-title" style="cursor: pointer" @click="outOfRangeOpen = !outOfRangeOpen">
        日期范围外的保留项目（{{ p.out_of_range_items.length }}）{{ outOfRangeOpen ? "▾" : "▸" }}
      </div>
      <div v-if="outOfRangeOpen" class="notice-block risk">
        以下项目已确认或锁定，但原始日期不在当前日历范围内；它们不会被静默删除，可在此解锁或取消。
      </div>
      <template v-if="outOfRangeOpen">
        <PlanItemCard
          v-for="item in p.out_of_range_items"
          :key="item.item_id"
          :item="item"
          :readonly="plan.viewingVersion !== null"
          show-date
        />
      </template>
    </template>

    <ReplanForm v-if="plan.viewingVersion === null" />
  </template>
</template>
