import type { ThemeConfig } from 'antd';

/**
 * 绎紫设计系统 · Premium SaaS（基于原绎紫规范演进）
 * 主色 绎紫 #5645D4 · 深海军蓝 #0A1530 · 品牌渐变 #5645D4 → #2F6BFF（克制使用）
 * 画布 云白 #F7F8FC · 卡片纯白 14px 圆角轻阴影 · 层级靠留白与阴影而非描边
 */
export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#5645D4',
    colorInfo: '#2F6BFF',
    colorSuccess: '#16A34A',
    colorWarning: '#DD5B00',
    colorError: '#E03131',
    colorLink: '#5645D4',
    borderRadius: 8,
    fontSize: 14,
    colorBgLayout: '#F7F8FC',
    colorBorderSecondary: '#EEF1F6',
    colorTextBase: '#101828',
    colorTextSecondary: '#475467',
    colorTextTertiary: '#98A2B3',
    boxShadow: '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)',
    boxShadowSecondary: '0 4px 12px rgba(16,24,40,0.08)',
  },
  components: {
    Layout: {
      siderBg: '#0A1530',
      headerBg: '#FFFFFF',
      bodyBg: '#F7F8FC',
    },
    Menu: {
      darkItemColor: 'rgba(255,255,255,0.62)',
      darkItemHoverColor: '#FFFFFF',
      darkItemHoverBg: 'rgba(255,255,255,0.06)',
      darkItemSelectedColor: '#FFFFFF',
      darkItemSelectedBg: 'linear-gradient(135deg,#5645D4 0%,#7B5CF5 100%)',
      darkItemBorderRadius: 10,
      darkItemMarginInline: 10,
    },
    Card: {
      borderRadiusLG: 14,
      boxShadowTertiary: '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)',
    },
    Button: {
      borderRadius: 8,
      controlHeight: 36,
      fontWeight: 500,
      primaryShadow: '0 2px 6px rgba(86,69,212,0.35)',
    },
    Input: { borderRadius: 8, controlHeight: 36 },
    Select: { borderRadius: 8, controlHeight: 36 },
    Tag: { borderRadiusSM: 6 },
    Table: {
      headerBg: '#F9FAFC',
      headerColor: '#475467',
      borderColor: '#EEF1F6',
      rowHoverBg: '#F7F5FE',
    },
    Tabs: { inkBarColor: '#5645D4', itemSelectedColor: '#5645D4' },
    Modal: { borderRadiusLG: 14 },
  },
};
