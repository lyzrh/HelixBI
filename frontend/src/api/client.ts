/** fetch 封装：JSON 请求 + JWT/工作区头注入 + 401 统一跳登录 */

import { message } from 'antd';

const BASE = '';

export const TOKEN_KEY = 'helix_token';
export const WS_KEY = 'helix_workspace_id';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getWorkspaceId(): string | null {
  return localStorage.getItem(WS_KEY);
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
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

async function request<T>(method: string, url: string, body?: unknown, isForm = false): Promise<T> {
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
    redirectToLogin();
    throw new Error('登录已过期，请重新登录');
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
