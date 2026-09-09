/** fetch 封装：JSON 请求 + 统一错误提示 */

import { message } from 'antd';

const BASE = '';

async function request<T>(method: string, url: string, body?: unknown, isForm = false): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) {
      init.body = body as FormData;
    } else {
      init.headers = { 'Content-Type': 'application/json; charset=utf-8' };
      init.body = JSON.stringify(body);
    }
  }
  const resp = await fetch(BASE + url, init);
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
