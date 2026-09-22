/** fetch 封装：JSON 请求 + JWT/工作区头注入 + 401 自动刷新 + 统一跳登录。
 *
 * Token 生命周期（Security Hardening V1）：
 * - access token 短期有效（默认 1h），401 时**先尝试用 refresh token 换一次**再重试原请求；
 * - refresh token 存 localStorage，刷新成功后**轮换**（旧的立即失效，后端有重放检测）；
 * - 刷新失败 / 本地没有 refresh token → 清理登录态并跳登录页；
 * - 并发请求同时 401 时只发起一次刷新（共享同一个 Promise），避免把 refresh 打成重放。
 */

import { message } from 'antd';

const BASE = '';

export const TOKEN_KEY = 'helix_token';
export const REFRESH_KEY = 'helix_refresh_token';
export const WS_KEY = 'helix_workspace_id';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function getWorkspaceId(): string | null {
  return localStorage.getItem(WS_KEY);
}

/** 保存令牌（登录与刷新共用） */
export function setAuth(accessToken: string, refreshToken?: string | null) {
  localStorage.setItem(TOKEN_KEY, accessToken);
  if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(WS_KEY);
}

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const wsId = getWorkspaceId();
  if (wsId) headers['X-Workspace-Id'] = wsId;
  return headers;
}

function redirectToLogin() {
  clearAuth();
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login';
  }
}

let refreshing: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  try {
    const resp = await fetch(`${BASE}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) return false;
    const data = (await resp.json()) as { token: string; refresh_token?: string };
    setAuth(data.token, data.refresh_token ?? null);
    return true;
  } catch {
    return false;
  }
}

/** 刷新 access token（并发去重）：成功返回 true */
export async function refreshAccessToken(): Promise<boolean> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => { refreshing = null; });
  }
  return refreshing;
}

function expireToLogin(): never {
  redirectToLogin();
  throw new Error('登录已过期，请重新登录');
}

async function request<T>(
  method: string,
  url: string,
  body?: unknown,
  isForm = false,
  allowRetry = true,
): Promise<T> {
  const init: RequestInit = { method, headers: { ...authHeaders() } };
  if (body !== undefined) {
    if (isForm) {
      init.body = body as FormData;
    } else {
      init.headers = { ...init.headers, 'Content-Type': 'application/json; charset=utf-8' };
      init.body = JSON.stringify(body);
    }
  }
  const resp = await fetch(BASE + url, init);

  if (resp.status === 401) {
    // access token 过期是最常见的 401：先换一次令牌再重试原请求
    if (allowRetry && await refreshAccessToken()) {
      return request<T>(method, url, body, isForm, false);
    }
    expireToLogin();
  }
  if (!resp.ok) {
    let detail = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      detail = data.detail || data.message || detail;
    } catch { /* ignore */ }
    message.error(detail);
    throw new Error(detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
  postForm: <T>(url: string, form: FormData) => request<T>('POST', url, form, true),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body),
  patch: <T>(url: string, body?: unknown) => request<T>('PATCH', url, body),
  del: <T>(url: string) => request<T>('DELETE', url),
};
