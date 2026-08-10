import type { ApiError } from "../types/api";

/**
 * Shared fetch wrapper. All requests are same-origin with credentials so the
 * HttpOnly session / guest capability cookies ride along automatically.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    ...options,
  });

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    throw toApiError(body, response.status);
  }

  // Some backend endpoints report domain failures with HTTP 200 and a
  // {status: "error"} body. Treat those exactly like HTTP failures so the
  // caller cannot accidentally continue as if a mutation succeeded.
  if (
    body &&
    typeof body === "object" &&
    (body as { status?: unknown }).status === "error"
  ) {
    throw toApiError(body, response.status);
  }

  return body as T;
}

export async function apiDownload(path: string, options: RequestInit = {}): Promise<Blob> {
  const response = await fetch(path, {
    credentials: "include",
    ...options,
  });
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* keep the HTTP fallback */
    }
    throw toApiError(body, response.status);
  }
  return response.blob();
}

function toApiError(body: unknown, httpStatus: number): ApiError {
  const record = body && typeof body === "object"
    ? (body as { message?: unknown; current_version?: unknown })
    : {};
  const err: ApiError = {
    status: "error",
    message:
      typeof record.message === "string" && record.message
        ? record.message
        : `请求失败（HTTP ${httpStatus}）`,
    httpStatus,
  };
  if (typeof record.current_version === "number") {
    err.current_version = record.current_version;
  }
  return err;
}

export function apiGet<T = unknown>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "GET" });
}

export function apiPostJson<T = unknown>(path: string, payload: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function apiPostForm<T = unknown>(path: string, form: FormData): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: form });
}

export function apiPatchJson<T = unknown>(path: string, payload: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function apiDelete<T = unknown>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE" });
}
