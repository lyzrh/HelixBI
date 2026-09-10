import type { ThemeConfig } from 'antd';

/**
 * 绎紫设计系统（Notion 系视觉规范）
 * 主色 绎紫 #5645D4 · 深海军蓝 #0A1530 · 数据青 #2A9D99 · 链路黄 #F5D75E
 * 画布 云雾灰 #F6F5F4 · 发丝线 #E5E3DF · 按钮 8px / 卡片 12px 圆角
 */
export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#5645D4',
    colorInfo: '#0075DE',
    colorSuccess: '#1AAE39',
    colorWarning: '#DD5B00',
    colorError: '#E03131',
    colorLink: '#0075DE',
    borderRadius: 8,
    fontSize: 14,
    colorBgLayout: '#F6F5F4',
    colorBorderSecondary: '#E5E3DF',
    colorTextBase: '#1A1A1A',
    colorTextSecondary: '#5D5B54',
    colorTextTertiary: '#787671',
  },
  components: {
    Layout: {
      siderBg: '#0A1530',
      headerBg: '#FFFFFF',
      bodyBg: '#F6F5F4',
    },
    Menu: {
      darkItemColor: '#A4A097',
      darkItemHoverColor: '#FFFFFF',
      darkItemHoverBg: 'rgba(255,255,255,0.06)',
      darkItemSelectedColor: '#FFFFFF',
      darkItemSelectedBg: '#1A2A52',
      darkItemBorderRadius: 8,
    },
    Card: {
      borderRadiusLG: 12,
      boxShadowTertiary: 'none',
    },
    Button: {
      borderRadius: 8,
      controlHeight: 36,
    },
    Tag: {
      borderRadiusSM: 6,
    },
  },
};
