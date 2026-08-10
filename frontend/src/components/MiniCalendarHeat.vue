<script setup lang="ts">
import { computed, ref } from "vue";
import { usePlanStore } from "../stores/plan";

const plan = usePlanStore();

/**
 * Decorative mini calendar for the empty chat state. Non-interactive cells:
 * every day uses one uniform color and plan days use a single highlight
 * color. Month paging jumps between months that actually contain plan days;
 * the arrow dims and is disabled when no plan exists in that direction.
 */

const DOW = ["一", "二", "三", "四", "五", "六", "日"];

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function monthKey(year: number, month0: number) {
  return `${year}-${pad(month0 + 1)}`;
}

const now = new Date();
const currentMonthKey = monthKey(now.getFullYear(), now.getMonth());
const todayStr = `${currentMonthKey}-${pad(now.getDate())}`;

/** Visible month cursor; defaults to the real current month. */
const viewMonth = ref(currentMonthKey);

/** Months (YYYY-MM) that contain at least one plan day, sorted ascending. */
const planMonths = computed<string[]>(() => {
  const set = new Set<string>();
  for (const d of plan.calendar) {
    if (d.date && (d.item_count ?? 0) >= 0) set.add(d.date.slice(0, 7));
  }
  return [...set].sort();
});

const planDates = computed(() => {
  const set = new Set<string>();
  for (const d of plan.calendar) set.add(d.date);
  return set;
});

/** Nearest plan month before/after the visible month. */
const prevTarget = computed(() => {
  const earlier = planMonths.value.filter((m) => m < viewMonth.value);
  return earlier.length ? earlier[earlier.length - 1] : null;
});
const nextTarget = computed(() => {
  const later = planMonths.value.find((m) => m > viewMonth.value);
  if (later) return later;
  // Allow returning to the real current month when browsing the past.
  if (viewMonth.value < currentMonthKey) return currentMonthKey;
  return null;
});

function goPrev() {
  if (prevTarget.value) viewMonth.value = prevTarget.value;
}
function goNext() {
  if (nextTarget.value) viewMonth.value = nextTarget.value;
}

const monthLabel = computed(() => {
  const [y, m] = viewMonth.value.split("-");
  return `${y} 年 ${Number(m)} 月`;
});

const hasPlanDays = computed(() => planDates.value.size > 0);

interface MiniCell {
  key: string;
  inPlan: boolean;
  isToday: boolean;
}

const cells = computed<MiniCell[]>(() => {
  const [year, month] = viewMonth.value.split("-").map(Number);
  const first = new Date(year, month - 1, 1);
  const offset = (first.getDay() + 6) % 7; // Monday first
  const daysInMonth = new Date(year, month, 0).getDate();
  const result: MiniCell[] = [];
  for (let i = 0; i < offset; i++) {
    result.push({ key: `pad-${i}`, inPlan: false, isToday: false });
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const date = `${viewMonth.value}-${pad(d)}`;
    result.push({
      key: date,
      inPlan: planDates.value.has(date),
      isToday: date === todayStr,
    });
  }
  return result;
});
</script>

<template>
  <div class="mini-heat" aria-hidden="true">
    <div class="mini-heat-head">
      <button
        class="mini-heat-nav"
        type="button"
        :disabled="!prevTarget"
        title="上一个有计划的月份"
        @click="goPrev"
      >‹</button>
      <span class="mini-heat-month">{{ monthLabel }}</span>
      <button
        class="mini-heat-nav"
        type="button"
        :disabled="!nextTarget"
        title="下一个有计划的月份"
        @click="goNext"
      >›</button>
    </div>
    <div class="mini-heat-dow">
      <span v-for="d in DOW" :key="d">{{ d }}</span>
    </div>
    <div class="mini-heat-grid">
      <span
        v-for="cell in cells"
        :key="cell.key"
        class="mini-heat-cell"
        :class="{ pad: cell.key.startsWith('pad-'), active: cell.inPlan, today: cell.isToday }"
      ></span>
    </div>
    <div class="mini-heat-caption">
      {{ hasPlanDays ? "高亮日期为行程日" : "生成计划后，行程日期会在这里点亮" }}
    </div>
  </div>
</template>
