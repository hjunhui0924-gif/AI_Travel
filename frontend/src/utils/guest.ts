const GUEST_THREAD_KEY = "ai_agent_guest_thread_id";
const GUEST_ID_PATTERN = /^guest_[0-9a-f]{16,64}$/;

function randomHex(length: number): string {
  const bytes = new Uint8Array(length / 2);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Load or mint a guest thread id: guest_<32 hex chars>. */
export function getOrCreateGuestThreadId(): string {
  const existing = localStorage.getItem(GUEST_THREAD_KEY);
  if (existing && GUEST_ID_PATTERN.test(existing)) {
    return existing;
  }
  const fresh = `guest_${randomHex(32)}`;
  localStorage.setItem(GUEST_THREAD_KEY, fresh);
  return fresh;
}

/** Start a brand-new guest conversation. */
export function resetGuestThreadId(): string {
  const fresh = `guest_${randomHex(32)}`;
  localStorage.setItem(GUEST_THREAD_KEY, fresh);
  return fresh;
}

/** Forget the browser pointer after a guest thread is claimed by an account. */
export function clearGuestThreadId(): void {
  localStorage.removeItem(GUEST_THREAD_KEY);
}

export function isGuestThread(threadId: string): boolean {
  return threadId.startsWith("guest_");
}
