/** 认证状态：JWT + UserContext + 工作区切换 */

import { create } from 'zustand';
import {
  api, clearAuth, getRefreshToken, getToken, setAuth, WS_KEY,
} from '../api/client';

export interface UserContextInfo {
  user_id: number;
  username: string;
  workspace_id: number;
  role: string;
  permissions: string[];
  data_scope: string;
}

export interface WorkspaceInfo {
  workspace_id: number;
  name: string;
  role_code: string;
  description: string;
}

interface AuthState {
  token: string | null;
  context: UserContextInfo | null;
  workspaces: WorkspaceInfo[];
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  loadMe: () => Promise<void>;
  switchWorkspace: (workspaceId: number) => Promise<void>;
  logout: () => void;
  hasPermission: (permission: string) => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: getToken(),
  context: null,
  workspaces: [],
  loading: false,

  login: async (username, password) => {
    set({ loading: true });
    try {
      const resp = await api.post<{
        token: string;
        refresh_token?: string;
        user: { username: string; display_name: string };
        workspaces: WorkspaceInfo[];
      }>('/api/auth/login', { username, password });
      // access + refresh 一起落本地：access 短期有效，401 时由 client 自动换新
      setAuth(resp.token, resp.refresh_token ?? null);
      // 默认工作区 = 后端返回的第一个成员关系（用户不能自选角色）
      const first = resp.workspaces[0];
      if (first) localStorage.setItem(WS_KEY, String(first.workspace_id));
      set({ token: resp.token, workspaces: resp.workspaces });
      await get().loadMe();
    } finally {
      set({ loading: false });
    }
  },

  loadMe: async () => {
    if (!getToken()) return;
    try {
      const resp = await api.get<{
        context: UserContextInfo;
        workspaces: WorkspaceInfo[];
        current_workspace_id: number;
      }>('/api/auth/me');
      localStorage.setItem(WS_KEY, String(resp.current_workspace_id));
      set({ context: resp.context, workspaces: resp.workspaces });
    } catch {
      // 401 已由 client 统一处理
    }
  },

  switchWorkspace: async (workspaceId) => {
    const resp = await api.post<{ context: UserContextInfo; current_workspace_id: number }>(
      '/api/auth/switch-workspace', { workspace_id: workspaceId });
    localStorage.setItem(WS_KEY, String(resp.current_workspace_id));
    set({ context: resp.context });
  },

  logout: () => {
    // 先让后端吊销 refresh token（令牌可撤销），再清本地态；失败也要清干净
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      api.post('/api/auth/logout', { refresh_token: refreshToken }).catch(() => undefined);
    }
    clearAuth();
    set({ token: null, context: null, workspaces: [] });
  },

  hasPermission: (permission) => {
    const ctx = get().context;
    return !!ctx && ctx.permissions.includes(permission);
  },
}));
