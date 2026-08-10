<script setup lang="ts">
import { computed } from "vue";
import type { TransportOption } from "../types/api";

const props = defineProps<{ option: TransportOption }>();

const MODE_LABELS: Record<string, string> = {
  flight: "航班",
  rail: "火车",
  train: "火车",
  driving: "驾车",
  transit: "公交",
  walking: "步行",
};

const modeLabel = computed(
  () => MODE_LABELS[props.option.mode] ?? props.option.mode,
);

const priceText = computed(() => {
  if (!props.option.price) return "";
  return props.option.price.startsWith("¥")
    ? props.option.price
    : `¥${props.option.price}`;
});
</script>

<template>
  <div class="transport-card" :class="{ 'is-demo': option.is_demo }">
    <div class="transport-route">
      <div>
        <span class="transport-time">{{ option.depart_time || "--:--" }}</span>
        <span class="transport-date">{{ option.depart_date }}</span>
      </div>
      <div class="transport-arrow">
        <span>{{ modeLabel }} · {{ option.duration }}</span>
      </div>
      <div style="text-align: right">
        <span class="transport-time">{{ option.arrive_time || "--:--" }}</span>
        <span class="transport-date">{{ option.arrive_date }}</span>
      </div>
    </div>
    <div class="ticket-divider" aria-hidden="true"></div>
    <div class="transport-meta">
      <span class="transport-title">{{ option.title }}</span>
      <span v-if="priceText" class="transport-price">{{ priceText }}</span>
    </div>
    <div v-if="option.seats?.length" class="transport-seats">
      {{ option.seats.join(" · ") }}
    </div>
    <div class="item-badges">
      <span v-if="option.provider" class="badge confidence">{{ option.provider }}</span>
      <span v-if="option.seat_count !== null && option.seat_count !== undefined" class="badge status-suggested">
        查询时库存 {{ option.seat_count }}（不代表锁座/出票）
      </span>
      <span v-if="option.is_demo" class="badge demo">演示数据，不可购票</span>
    </div>
  </div>
</template>
