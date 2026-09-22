/** 对话状态：会话列表、当前会话、消息、流式状态机 */

import { create } from 'zustand';
import { api } from '../api/client';
import { sseStream } from '../api/sse';
import type { ChatMessage, SessionInfo, StreamingState } from '../api/types';

interface ChatState {
  sessions: SessionInfo[];
  currentSession: SessionInfo | null;
  messages: ChatMessage[];
  streaming: StreamingState | null;
  selectedDsIds: number[];
  confirmSpec: { question: string; spec: Record<string, unknown> } | null;

  loadSessions: () => Promise<void>;
  openSession: (id: number) => Promise<void>;
  createSession: (agentId?: number | null) => Promise<SessionInfo>;
  deleteSession: (id: number) => Promise<void>;
  setSelectedDsIds: (ids: number[]) => void;
  setConfirmSpec: (v: ChatState['confirmSpec']) => void;

  ask: (question: string, spec?: Record<string, unknown> | null) => Promise<void>;
  rerun: (runId: number) => Promise<void>;
  runUrl: string | null; // 当前 SSE 目标（用于取消）
  abort: () => void;
  refreshCurrent: () => Promise<void>;
  _consume: (evt: { event: string; data: unknown }) => void;
}

const emptyStreaming = (): StreamingState => ({
  running: true, steps: [], attempts: 0, charts: [], tables: {}, followups: [],
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function applyEvent(st: StreamingState, evt: { event: string; data: any }): StreamingState {
  const next = { ...st };
  switch (evt.event) {
    case 'step': {
      const d = evt.data as unknown as { node: string; label: string; status: string; detail?: string };
      const steps = [...next.steps];
      const idx = steps.findIndex((s) => s.node === d.node);
      const item = { node: d.node, label: d.label, status: d.status as 'running' | 'done' | 'error', detail: d.detail };
      if (idx >= 0) steps[idx] = item; else steps.push(item);
      next.steps = steps;
      break;
    }
    case 'spec':
      next.spec = evt.data.spec as StreamingState['spec'];
      break;
    case 'code': {
      const d = evt.data as { plan: string; code: string; attempt: number };
      next.plan = d.plan; next.code = d.code; next.attempts = d.attempt;
      break;
    }
    case 'execute': {
      const d = evt.data as { ok: boolean; stdout: string; stderr: string; charts: string[]; tables: Record<string, Record<string, unknown>[]> };
      next.stdout = d.stdout; next.stderr = d.stderr; next.charts = d.charts; next.tables = d.tables;
      break;
    }
    case 'answer':
      next.answer = evt.data.answer as string;
      break;
    case 'followups':
      next.followups = evt.data.followups as string[];
      break;
    case 'charts':
      next.charts = evt.data.charts as string[];
      break;
    case 'tables':
      next.tables = evt.data.tables as Record<string, Record<string, unknown>[]>;
      break;
    case 'error':
      next.running = false;
      next.error = evt.data.message as string;
      break;
    case 'done':
      next.running = false;
      break;
    case 'state': {
      // Production Runtime V1 生命周期：queued / preparing / running / repairing /
      // validating / completed + 异常态 cancelled / timeout / resource_limited / failed。
      // 异常态必须终结 loading（后端保证每个 run 都会落到一个终态）。
      const d = evt.data as { phase: string; reason?: string; timeout_type?: string };
      next.phase = d.phase;
      if (d.reason) next.phaseReason = d.reason;
      if (['cancelled', 'timeout', 'resource_limited', 'failed'].includes(d.phase)) {
        next.running = false;
        if (d.phase === 'timeout') {
          next.error = `运行超时（${d.timeout_type === 'queue' ? '排队' : '执行'}超时）${d.reason ? `：${d.reason}` : ''}`;
        } else if (d.phase === 'resource_limited') {
          next.error = d.reason || '资源繁忙，请稍后再试';
        } else if (d.phase === 'cancelled') {
          next.error = '运行已取消';
        }
      }
      break;
    }
  }
  return next;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  currentSession: null,
  messages: [],
  streaming: null,
  selectedDsIds: [],
  confirmSpec: null,
  runUrl: null,

  loadSessions: async () => {
    try {
      set({ sessions: await api.get<SessionInfo[]>('/api/sessions') });
    } catch { /* 静默 */ }
  },

  openSession: async (id) => {
    set({ streaming: null, confirmSpec: null });
    try {
      const s = await api.get<SessionInfo>(`/api/sessions/${id}`);
      set({ currentSession: s, messages: s.messages || [] });
      if (s.agent?.data_source_ids?.length) set({ selectedDsIds: s.agent.data_source_ids });
    } catch { /* 静默 */ }
  },

  createSession: async (agentId) => {
    const s = await api.post<SessionInfo>('/api/sessions', { agent_id: agentId ?? null });
    await get().loadSessions();
    set({ currentSession: s, messages: [], streaming: null });
    return s;
  },

  deleteSession: async (id) => {
    await api.del(`/api/sessions/${id}`);
    await get().loadSessions();
    if (get().currentSession?.id === id) set({ currentSession: null, messages: [] });
  },

  setSelectedDsIds: (ids) => set({ selectedDsIds: ids }),
  setConfirmSpec: (v) => set({ confirmSpec: v }),

  refreshCurrent: async () => {
    const cur = get().currentSession;
    if (cur) await get().openSession(cur.id);
    await get().loadSessions();
  },

  abort: () => { /* fetch abort 由组件层 AbortController 管理 */ },

  /** SSE 事件统一消费：更新 streaming，done/error 后拉取持久化消息 */
  _consume: (evt) => {
    const st = get().streaming;
    if (!st) return;
    const next = applyEvent(st, evt);
    if (!next.running && (evt.event === 'done' || evt.event === 'error')) {
      set({ streaming: next });
      get().refreshCurrent().then(() => set({ streaming: null }));
    } else {
      set({ streaming: next });
    }
  },

  ask: async (question, spec) => {
    const cur = get().currentSession;
    if (!cur) return;
    const dsIds = get().selectedDsIds;
    if (!dsIds.length) return;

    set({
      streaming: emptyStreaming(),
      confirmSpec: null,
      messages: [...get().messages,
        { id: -Date.now(), session_id: cur.id, role: 'user', content: question, meta: {}, created_at: '' }],
    });

    await sseStream(
      `/api/sessions/${cur.id}/analyze`,
      { question, data_source_ids: dsIds, spec: spec ?? null, agent_id: cur.agent_id },
      get()._consume,
    );
  },

  rerun: async (runId) => {
    if (!get().currentSession) return;
    set({ streaming: emptyStreaming(), confirmSpec: null });
    await sseStream(`/api/runs/${runId}/rerun`, {}, get()._consume);
  },
}));
