<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { usePlanStore } from "../stores/plan";

const plan = usePlanStore();

const DOW = ["一", "二", "三", "四", "五", "六", "日"];

/** Visible month, defaults to the plan start month. */
const monthCursor = ref("");

const dayMap = computed(() => {
  const map = new Map<string, { selected: boolean; hasConflicts: boolean }>();
  for (const d of plan.calendar) {
    map.set(d.date, { selected: false, hasConflicts: d.has_conflicts });
  }
  return map;
});

const effectiveCursor = computed(() => {
  if (monthCursor.value) return monthCursor.value;
  const first = plan.calendar[0]?.date;
  return first ? first.slice(0, 7) : formatDateInShanghai(new Date()).slice(0, 7);
});

watch(
  () => [
    plan.displayPlan?.plan_id ?? "",
    plan.displayPlan?.version ?? 0,
    plan.selectedDate,
  ],
  () => {
    const date = plan.selectedDate || plan.calendar[0]?.date;
    if (date) monthCursor.value = date.slice(0, 7);
  },
);

interface Cell {
  date: string;
  day: number;
  otherMonth: boolean;
  inPlan: boolean;
  hasConflicts: boolean;
  isToday: boolean;
}

const todayStr = formatDateInShanghai(new Date());

const cells = computed<Cell[]>(() => {
  const [year, month] = effectiveCursor.value.split("-").map(Number);
  const firstOfMonth = new Date(year, month - 1, 1);
  // Monday-first offset
  const offset = (firstOfMonth.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month, 0).getDate();
  const result: Cell[] = [];

  const prevMonthDays = new Date(year, month - 1, 0).getDate();
  for (let i = offset - 1; i >= 0; i--) {
    const d = new Date(year, month - 2, prevMonthDays - i);
    result.push(buildCell(d, true));
  }
  for (let d = 1; d <= daysInMonth; d++) {
    result.push(buildCell(new Date(year, month - 1, d), false));
  }
  while (result.length % 7 !== 0) {
    const last = result[result.length - 1];
    const d = parseDate(last.date);
    d.setDate(d.getDate() + 1);
    result.push(buildCell(d, true));
  }
  return result;

  function buildCell(d: Date, otherMonth: boolean): Cell {
    const date = formatDate(d);
    const info = dayMap.value.get(date);
    return {
      date,
      day: d.getDate(),
      otherMonth,
      inPlan: !!info,
      hasConflicts: info?.hasConflicts ?? false,
      isToday: date === todayStr,
    };
  }
});

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatDateInShanghai(d: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function shiftMonth(delta: number) {
  const [year, month] = effectiveCursor.value.split("-").map(Number);
  const d = new Date(year, month - 1 + delta, 1);
  monthCursor.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

const monthLabel = computed(() => {
  const [year, month] = effectiveCursor.value.split("-");
  return `${year} 年 ${Number(month)} 月`;
});

function onPick(cell: Cell) {
  if (!cell.inPlan) return;
  plan.selectDate(cell.date);
}
</script>

<template>
  <div class="calendar">
    <div class="calendar-head">
      <button class="icon-btn" style="padding: 4px" type="button" @click="shiftMonth(-1)">‹</button>
      <span class="calendar-month">{{ monthLabel }}</span>
      <button class="icon-btn" style="padding: 4px" type="button" @click="shiftMonth(1)">›</button>
    </div>
    <div class="calendar-grid">
      <span v-for="d in DOW" :key="d" class="calendar-dow">{{ d }}</span>
      <button
        v-for="cell in cells"
        :key="cell.date"
        class="calendar-cell"
        type="button"
        :class="{
          'other-month': cell.otherMonth,
          'in-plan': cell.inPlan,
          selected: plan.selectedDate === cell.date,
          'has-conflict': cell.hasConflicts,
          today: cell.isToday,
        }"
        @click="onPick(cell)"
      >
        {{ cell.day }}
      </button>
    </div>
    <div v-if="plan.calendarTruncated" class="calendar-truncated-note">
      行程超过 31 天，日历仅投影至 {{ plan.projectionEndDate }}；完整日期范围以计划头为准。
    </div>
  </div>
</template>
