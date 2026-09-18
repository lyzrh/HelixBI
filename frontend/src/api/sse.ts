/** SSE 流消费：fetch POST + ReadableStream 手写解析（EventSource 不支持 POST） */

import type { SseEvent } from './types';
import { authHeaders } from './client';

export async function sseStream(
  url: string,
  body: unknown,
  onEvent: (evt: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(body),
    signal,
  });
  if (resp.status === 401) {
    onEvent({ event: 'error', data: { message: '登录已过期，请重新登录' } });
    return;
  }
  if (!resp.ok || !resp.body) {
    let detail = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch { /* ignore */ }
    onEvent({ event: 'error', data: { message: detail } });
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 按空行切帧；': ping' 注释行忽略
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const lines = frame.split('\n');
      let eventName = '';
      let dataStr = '';
      for (const line of lines) {
        if (line.startsWith(':')) continue; // 注释/心跳
        if (line.startsWith('event: ')) eventName = line.slice(7).trim();
        else if (line.startsWith('data: ')) dataStr += line.slice(6);
      }
      if (eventName && dataStr) {
        try {
          onEvent({ event: eventName, data: JSON.parse(dataStr) } as SseEvent);
        } catch { /* 忽略坏帧 */ }
      }
    }
  }
}
