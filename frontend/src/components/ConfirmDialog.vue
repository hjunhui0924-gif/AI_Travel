<script setup lang="ts">
defineProps<{
  title: string;
  text: string;
  confirmText?: string;
  busy?: boolean;
}>();
const emit = defineEmits<{ cancel: []; confirm: [] }>();
</script>

<template>
  <div class="modal-overlay" @click="emit('cancel')"></div>
  <section class="confirm-modal" role="alertdialog" aria-modal="true">
    <div class="confirm-title">{{ title }}</div>
    <div class="confirm-text">{{ text }}</div>
    <div class="confirm-actions">
      <button class="confirm-btn" type="button" @click="emit('cancel')">取消</button>
      <button
        class="confirm-btn danger"
        type="button"
        :disabled="busy"
        @click="emit('confirm')"
      >
        {{ busy ? "处理中..." : confirmText || "确定" }}
      </button>
    </div>
  </section>
</template>
