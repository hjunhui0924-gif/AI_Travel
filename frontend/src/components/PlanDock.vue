<script setup lang="ts">
defineProps<{ hasMessages?: boolean }>();
const emit = defineEmits<{ open: [] }>();
</script>

<template>
  <button
    class="workspace-plan-dock"
    :class="{ 'has-messages': hasMessages }"
    type="button"
    aria-label="打开行程计划"
    aria-haspopup="dialog"
    @click="emit('open')"
  >
    <span class="workspace-plan-dock-icon">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="4" y="5" width="16" height="15" rx="2" />
        <path d="M8 3v4M16 3v4M4 10h16M8 14h3M8 17h5" />
      </svg>
    </span>
    <span class="workspace-plan-dock-copy">
      <strong>行程计划</strong>
      <small>{{ hasMessages ? "查看当前对话的旅行安排" : "先探索，再把灵感装进行程" }}</small>
    </span>
    <svg class="workspace-plan-dock-arrow" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 12h10" />
      <path d="m13 7 5 5-5 5" />
    </svg>
  </button>
</template>
