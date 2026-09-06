<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useChatStore } from "../stores/chat";
import { useSessionStore } from "../stores/session";

const chat = useChatStore();
const session = useSessionStore();
const input = ref("");
const textareaEl = ref<HTMLTextAreaElement | null>(null);
const fileInputEl = ref<HTMLInputElement | null>(null);
const ready = computed(() => Boolean(session.threadId));

function autoGrow() {
  const el = textareaEl.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

async function send() {
  const text = input.value;
  if (!text.trim() && !chat.pendingFiles.length) return;
  if (chat.loading) return;
  input.value = "";
  await nextTick();
  autoGrow();
  await chat.send(text);
}

function stop() {
  chat.stopGeneration();
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
}

function onFilesPicked(e: Event) {
  const target = e.target as HTMLInputElement;
  if (target.files?.length) chat.addFiles(target.files);
  target.value = "";
}

function onPaste(e: ClipboardEvent) {
  const files = e.clipboardData?.files;
  if (files?.length) {
    chat.addFiles(files);
    e.preventDefault();
  }
}
</script>

<template>
  <div class="composer">
    <div class="composer-inner">
      <div v-if="chat.pendingFiles.length" class="file-chip-list">
        <span v-for="(f, i) in chat.pendingFiles" :key="i" class="file-chip">
          {{ f.name }}
          <button type="button" title="移除" @click="chat.removeFile(i)">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              stroke-width="2.4" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </span>
      </div>
      <div v-if="chat.fileError" class="file-error">{{ chat.fileError }}</div>
      <textarea
        ref="textareaEl"
        v-model="input"
        rows="1"
        placeholder="例如：我国庆想带爸妈从广州去杭州玩 4 天，慢节奏，多安排美食"
        :disabled="!ready"
        @input="autoGrow"
        @keydown="onKeydown"
        @paste="onPaste"
      ></textarea>
      <div class="composer-row">
        <button
          class="icon-btn"
          type="button"
          aria-label="上传文件或图片"
          title="上传文件或图片"
          :disabled="!ready"
          @click="fileInputEl?.click()"
        >
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21.2 15c.7-1.2 1-2.5.7-3.9-.6-2-2.4-3.5-4.4-3.5h-1.2c-.7-3-3.2-5.2-6.2-5.6-3-.3-5.9 1.3-7.3 4-1.2 2.5-1 6.5.5 8.4" />
            <path d="M12 22v-9" />
            <path d="M8 17l4-4 4 4" />
          </svg>
        </button>
        <input
          ref="fileInputEl"
          type="file"
          multiple
          class="hidden"
          accept=".pdf,.txt,.md,.csv,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg,.webp,.gif"
          @change="onFilesPicked"
        />
        <div class="composer-spacer"></div>
        <button
          class="search-toggle"
          :class="{ active: chat.searchEnabled }"
          type="button"
          :disabled="!ready"
          title="开启后允许联网搜索（仅用于地点发现与热度信号）"
          @click="chat.searchEnabled = !chat.searchEnabled"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2.2" stroke-linecap="round">
            <circle cx="11" cy="11" r="8" />
            <path d="M21 21l-4.35-4.35" />
          </svg>
          <span>联网搜索{{ chat.searchEnabled ? "已开" : "" }}</span>
        </button>
        <button
          class="send-btn"
          :class="{ 'is-stop': chat.loading }"
          type="button"
          :aria-label="chat.loading ? '停止生成' : '发送消息'"
          :title="chat.loading ? '停止生成' : '发送消息'"
          :disabled="!ready || (!chat.loading && (!input.trim() && !chat.pendingFiles.length))"
          @click="chat.loading ? stop() : send()"
        >
          <svg v-if="!chat.loading" width="17" height="17" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
          <svg v-else class="stop-icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"
            aria-hidden="true">
            <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
          </svg>
        </button>
      </div>
    </div>
    <div class="composer-hint">
      支持 PDF、Word、Excel、图片（单个最大 10MB）；出行类问题会生成结构化行程，可在右侧计划面板查看与管理。
    </div>
  </div>
</template>
