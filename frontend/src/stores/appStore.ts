/** 全局状态：健康信息、统计、数据源列表、个人资料 */

import { create } from 'zustand';
import { api } from '../api/client';
import type { DataSourceInfo, HealthInfo, Stats } from '../api/types';

export interface UserProfile {
  nickname: string;
  role: string;
  avatar_color: string;
}

interface AppState {
  health: HealthInfo | null;
  stats: Stats | null;
  dataSources: DataSourceInfo[];
  packs: { id: string; name: string }[];
  profile: UserProfile;
  loadHealth: () => Promise<void>;
  loadStats: () => Promise<void>;
  loadDataSources: () => Promise<void>;
  loadPacks: () => Promise<void>;
  loadProfile: () => Promise<void>;
}

const DEFAULT_PROFILE: UserProfile = { nickname: '数据探索者', role: '数据分析师', avatar_color: '#5645D4' };

export const useAppStore = create<AppState>((set) => ({
  health: null,
  stats: null,
  dataSources: [],
  packs: [],
  profile: DEFAULT_PROFILE,

  loadHealth: async () => {
    try {
      set({ health: await api.get<HealthInfo>('/api/health') });
    } catch { /* 静默 */ }
  },
  loadStats: async () => {
    try {
      set({ stats: await api.get<Stats>('/api/stats') });
    } catch { /* 静默 */ }
  },
  loadDataSources: async () => {
    try {
      set({ dataSources: await api.get<DataSourceInfo[]>('/api/datasources') });
    } catch { /* 静默 */ }
  },
  loadPacks: async () => {
    try {
      set({ packs: await api.get<{ id: string; name: string }[]>('/api/semantic/packs') });
    } catch { /* 静默 */ }
  },
  loadProfile: async () => {
    try {
      set({ profile: await api.get<UserProfile>('/api/settings/profile') });
    } catch { /* 静默 */ }
  },
}));
