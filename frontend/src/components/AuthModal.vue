<script setup lang="ts">
import { computed, ref } from "vue";
import { useAuthStore } from "../stores/auth";
import { useSessionStore } from "../stores/session";

const props = defineProps<{ mode: "login" | "register" }>();
const emit = defineEmits<{ close: []; success: [] }>();

const auth = useAuthStore();
const session = useSessionStore();
const mode = ref(props.mode);
const username = ref("");
const password = ref("");
const displayName = ref("");
const feedback = ref("");
const busy = ref(false);

const isLogin = computed(() => mode.value === "login");

async function submit() {
  feedback.value = "";
  if (!username.value.trim() || !password.value) {
    feedback.value = "请输入用户名和密码";
    return;
  }
  busy.value = true;
  try {
    const guestThreadId = session.isGuest ? session.threadId : "";
    const guestTitle = session.isGuest ? session.currentTitle : "";
    if (isLogin.value) {
      await auth.login(username.value.trim(), password.value, guestThreadId, guestTitle);
    } else {
      await auth.register(
        username.value.trim(),
        password.value,
        displayName.value.trim(),
        guestThreadId,
        guestTitle,
      );
    }
    emit("success");
  } catch (e) {
    feedback.value =
      e instanceof Error
        ? e.message
        : e && typeof e === "object" && "message" in e
          ? String((e as { message?: unknown }).message || "操作失败，请重试")
          : "操作失败，请重试";
  } finally {
    busy.value = false;
  }
}

function switchMode() {
  mode.value = isLogin.value ? "register" : "login";
  feedback.value = "";
}
</script>

<template>
  <div class="modal-overlay" @click="emit('close')"></div>
  <section class="auth-modal" role="dialog" aria-modal="true">
    <div class="auth-modal-header">
      <div>
        <div class="auth-eyebrow">Account Access</div>
        <h2 class="auth-title">{{ isLogin ? "登录账号" : "注册账号" }}</h2>
      </div>
      <button class="icon-btn" title="关闭" @click="emit('close')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2.2" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <form @submit.prevent="submit">
      <label class="auth-field">
        <span>用户名</span>
        <input v-model="username" class="auth-input" type="text" autocomplete="username" />
      </label>
      <label v-if="!isLogin" class="auth-field">
        <span>显示名称（可选）</span>
        <input v-model="displayName" class="auth-input" type="text" autocomplete="nickname" />
      </label>
      <label class="auth-field">
        <span>密码</span>
        <input
          v-model="password"
          class="auth-input"
          type="password"
          :autocomplete="isLogin ? 'current-password' : 'new-password'"
          placeholder="至少 6 位"
        />
      </label>
      <div v-if="feedback" class="auth-feedback">{{ feedback }}</div>
      <button class="auth-submit" type="submit" :disabled="busy">
        {{ busy ? "处理中..." : isLogin ? "登录" : "注册" }}
      </button>
    </form>
    <div class="auth-switcher">
      <span>{{ isLogin ? "还没有账号？" : "已有账号？" }}</span>
      <button type="button" @click="switchMode">
        {{ isLogin ? "去注册" : "去登录" }}
      </button>
    </div>
  </section>
</template>
