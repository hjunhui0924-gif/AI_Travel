<script setup lang="ts">
import { computed } from "vue";
import type { BookingRequirement, OpeningWindow, PlanItem } from "../types/api";
import { usePlanStore } from "../stores/plan";

const props = withDefaults(
  defineProps<{ item: PlanItem; readonly?: boolean; showDate?: boolean }>(),
  { readonly: false, showDate: false },
);

const plan = usePlanStore();

const openingWindow = computed<OpeningWindow | null>(() => {
  const itemId = props.item.opening_window_id;
  return plan.displayPlan?.opening_windows?.find((window) =>
    Boolean(itemId && window.target_id === props.item.place_id && window.date === props.item.date),
  ) ?? null;
});
const bookingRequirement = computed<BookingRequirement | null>(() => {
  const itemId = props.item.booking_requirement_id;
  return plan.displayPlan?.booking_requirements?.find((booking) => Boolean(itemId && booking.target_id === itemId)) ?? null;
});

const TYPE_ICONS: Record<string, string> = {
  transport: "✈",
  transport_arrival: "⇥",
  accommodation: "🏨",
  hotel: "🏨",
  attraction: "📍",
  activity: "📍",
  dining: "🍜",
  restaurant: "🍜",
  rest: "☕",
  reminder: "⏰",
};

const STATUS_LABELS: Record<string, string> = {
  suggested: "建议",
  confirmed: "已确认",
  booked: "已预订",
  skipped: "已跳过",
  cancelled: "已取消",
};

const icon = computed(() => TYPE_ICONS[props.item.item_type] ?? "•");
const statusLabel = computed(
  () => STATUS_LABELS[props.item.status] ?? props.item.status,
);

const timeText = computed(() => {
  const parts: string[] = [];
  if (props.showDate) parts.push(props.item.date);
  if (props.item.start_time) {
    parts.push(
      props.item.end_time
        ? `${props.item.start_time} – ${props.item.end_time}`
        : props.item.start_time,
    );
  }
  return parts.join(" · ");
});

const overnight = computed(
  () => props.item.end_date && props.item.end_date !== props.item.date,
);

const busy = computed(
  () => Boolean(plan.patchingItemId) || plan.replanning,
);

const canConfirm = computed(
  () => !props.item.locked && !["confirmed", "booked"].includes(props.item.status),
);
const canBook = computed(
  () => props.item.status === "confirmed" && props.item.locked,
);
const canUnlock = computed(() => props.item.locked);
const canSkip = computed(
  () => !["skipped", "cancelled"].includes(props.item.status),
);
const canRestore = computed(() =>
  ["skipped", "cancelled"].includes(props.item.status),
);

function confirm() {
  // confirmed/booked must be locked=true (handoff §7)
  plan.patchItem(props.item, { status: "confirmed", locked: true });
}
function book() {
  plan.patchItem(props.item, { status: "booked", locked: true });
}
function unlock() {
  // locked=false alone resets the item to suggested
  plan.patchItem(props.item, { locked: false });
}
function skip() {
  plan.patchItem(props.item, { status: "skipped" });
}
function restore() {
  plan.patchItem(props.item, { status: "suggested" });
}
</script>

<template>
  <div
    class="plan-item"
    :class="{
      'is-locked': item.locked,
      'is-demo': item.is_demo,
      'is-skipped': item.status === 'skipped',
      'is-cancelled': item.status === 'cancelled',
    }"
  >
    <div class="item-icon">{{ icon }}</div>
    <div class="item-main">
      <div class="item-title">{{ item.title }}</div>
      <div v-if="timeText" class="item-time">{{ timeText }}</div>
      <div v-if="item.detail" class="item-detail">{{ item.detail }}</div>
      <div v-if="item.location || item.address" class="item-detail">
        {{ item.location || item.address }}
      </div>
      <div v-if="item.estimated_cost" class="item-detail">
        预估费用：{{ item.estimated_cost }}
      </div>
      <div v-if="openingWindow && (openingWindow.open_time || openingWindow.close_time)" class="item-detail">
        开放时间：{{ openingWindow.open_time || "待确认" }}–{{ openingWindow.close_time || "待确认" }}
        <span v-if="openingWindow.last_entry_time"> · 最后入场 {{ openingWindow.last_entry_time }}</span>
      </div>
      <div v-if="bookingRequirement" class="item-detail booking-note">
        {{ bookingRequirement.required || bookingRequirement.booking_status === "required" ? "需要预约/购票" : "预约状态" }}：
        {{ bookingRequirement.booking_status === "unknown" ? "尚未确认" : bookingRequirement.booking_status }}
        <span v-if="bookingRequirement.booking_note"> · {{ bookingRequirement.booking_note }}</span>
        <a
          v-if="bookingRequirement.booking_url"
          :href="bookingRequirement.booking_url"
          target="_blank"
          rel="noopener noreferrer"
        >官方入口</a>
      </div>
      <div v-if="item.buffer_minutes" class="item-detail">
        交通缓冲：{{ item.buffer_minutes }} 分钟<span v-if="item.walking_minutes !== null && item.walking_minutes !== undefined"> · 步行约 {{ item.walking_minutes }} 分钟</span>
      </div>
      <div v-if="item.seat_count !== null && item.seat_count !== undefined" class="item-detail">
        查询时余票/库存：{{ item.seat_count }}（仅供参考，不代表锁座或出票）
      </div>
      <div class="item-badges">
        <span class="badge" :class="`status-${item.status}`">{{ statusLabel }}</span>
        <span v-if="item.locked" class="badge locked">已锁定</span>
        <span v-if="item.is_demo" class="badge demo">演示数据，不可购票</span>
        <span v-if="overnight" class="badge overnight">跨日到达 {{ item.end_date }}</span>
        <span v-if="item.confidence && item.confidence !== 'unknown'" class="badge confidence">
          {{ item.confidence === "source_backed" ? "有来源" : item.confidence }}
        </span>
      </div>
      <div v-if="!readonly" class="item-actions">
        <button
          v-if="canConfirm"
          class="item-action-btn primary"
          type="button"
          :disabled="busy"
          @click="confirm"
        >
          确认并锁定
        </button>
        <button
          v-if="canBook"
          class="item-action-btn primary"
          type="button"
          :disabled="busy"
          @click="book"
        >
          标记已预订
        </button>
        <button
          v-if="canUnlock"
          class="item-action-btn"
          type="button"
          :disabled="busy"
          @click="unlock"
        >
          解锁
        </button>
        <button
          v-if="canSkip"
          class="item-action-btn"
          type="button"
          :disabled="busy"
          @click="skip"
        >
          跳过
        </button>
        <button
          v-if="canRestore"
          class="item-action-btn"
          type="button"
          :disabled="busy"
          @click="restore"
        >
          恢复为建议
        </button>
      </div>
    </div>
  </div>
</template>
