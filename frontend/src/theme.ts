import type { ThemeConfig } from 'antd';

/** Marvis 风格：主色 #2563eb，浅灰底白卡片 */
export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2563eb',
    borderRadius: 8,
    fontSize: 14,
    colorBgLayout: '#f5f7fa',
  },
  components: {
    Layout: {
      siderBg: '#ffffff',
      headerBg: '#ffffff',
      bodyBg: '#f5f7fa',
    },
    Card: {
      boxShadowTertiary: '0 1px 2px rgba(15, 23, 42, 0.04)',
    },
  },
};
