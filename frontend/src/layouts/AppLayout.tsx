import { Avatar, Layout, Menu, Tag, Tooltip } from 'antd';
import {
  DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  MessageOutlined, RocketOutlined, SettingOutlined, SoundOutlined,
  ThunderboltOutlined, PieChartOutlined,
} from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { SettingsModal } from '../components/SettingsModal';
import { HelixBadge } from '../components/HelixMark';
import { useAppStore } from '../stores/appStore';
import { useT, type StrKey } from '../i18n';

const NAV = [
  { key: '/', icon: <RocketOutlined />, labelKey: 'nav.workbench' as StrKey },
  { key: '/chat', icon: <MessageOutlined />, labelKey: 'nav.chat' as StrKey },
  { key: '/explore', icon: <PieChartOutlined />, labelKey: 'nav.explore' as StrKey },
  { key: '/agents', icon: <ThunderboltOutlined />, labelKey: 'nav.agents' as StrKey },
  { key: '/skills', icon: <ExperimentOutlined />, labelKey: 'nav.skills' as StrKey },
  { key: '/insights', icon: <SoundOutlined />, labelKey: 'nav.insights' as StrKey },
  { key: '/dashboards', icon: <DashboardOutlined />, labelKey: 'nav.dashboards' as StrKey },
  { key: '/datasources', icon: <DatabaseOutlined />, labelKey: 'nav.datasources' as StrKey },
];

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const t = useT();
  const health = useAppStore((s) => s.health);
  const profile = useAppStore((s) => s.profile);
  const loadProfile = useAppStore((s) => s.loadProfile);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => { loadProfile(); }, [loadProfile]);

  const navKey = location.pathname.startsWith('/chat') ? '/chat' : location.pathname;
  const selected = NAV.find((n) => n.key === navKey);
  const pageTitle = selected ? t(selected.labelKey) : 'Helix BI';

  const menuItems = NAV.map((n) => ({ key: n.key, icon: n.icon, label: t(n.labelKey) }));

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider width={208} theme="dark"
        style={{
          position: 'fixed', left: 0, top: 0, bottom: 0, overflow: 'auto',
          background: '#0A1530',
        }}>
        <div style={{ padding: '20px 16px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <HelixBadge size={34} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, color: '#fff' }}>绎数</div>
            <div style={{ fontSize: 10, letterSpacing: 2, color: 'rgba(255,255,255,0.45)' }}>HELIX BI</div>
          </div>
        </div>
        <div style={{ height: 1, background: '#1A2A52', margin: '0 16px 8px' }} />
        <Menu mode="inline" theme="dark" selectedKeys={[navKey]} items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', padding: '0 8px', background: 'transparent' }} />
        <div style={{ position: 'absolute', bottom: 16, left: 16, right: 16 }}>
          <div style={{
            fontSize: 12, color: '#A4A097', display: 'flex', flexDirection: 'column', gap: 6,
            background: '#131F42', borderRadius: 8, padding: '10px 12px',
          }}>
            <span>
              {t('status.sandbox')}：
              {health?.sandbox
                ? <Tag color="success" style={{ marginLeft: 4 }}>{t('status.online')}</Tag>
                : <Tag color="warning" style={{ marginLeft: 4 }}>{t('status.offline')}</Tag>}
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              {t('status.model')}：
              <Tooltip title={t('status.tipSettings')}>
                <Tag
                  color={health?.llm_configured ? 'processing' : 'error'}
                  style={{ marginLeft: 4, cursor: 'pointer' }}
                  onClick={() => setSettingsOpen(true)}
                >
                  {health?.llm_configured ? (health.model || t('status.online')) : t('status.notConfigured')}
                </Tag>
              </Tooltip>
            </span>
          </div>
        </div>
      </Layout.Sider>
      <Layout style={{ marginLeft: 208 }}>
        {/* 顶栏：页面标题 + 右侧用户区 */}
        <Layout.Header style={{
          height: 52, lineHeight: '52px', background: '#fff',
          borderBottom: '1px solid #eef2f7', padding: '0 20px',
          display: 'flex', alignItems: 'center', gap: 10,
          position: 'sticky', top: 0, zIndex: 100,
        }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>{pageTitle}</span>
          <div style={{ flex: 1 }} />
          <Tooltip title={t('status.tipSettings')}>
            <SettingOutlined
              style={{ fontSize: 17, color: '#64748b', cursor: 'pointer' }}
              onClick={() => setSettingsOpen(true)}
            />
          </Tooltip>
          <Tooltip title={`${profile.nickname} · ${profile.role}`}>
            <div
              style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '0 2px' }}
              onClick={() => setSettingsOpen(true)}
            >
              <Avatar size={32} style={{ background: profile.avatar_color, fontWeight: 600, fontSize: 14 }}>
                {(profile.nickname || 'U').slice(0, 1)}
              </Avatar>
              <span style={{ fontSize: 13, color: '#334155' }}>{profile.nickname}</span>
            </div>
          </Tooltip>
        </Layout.Header>
        <Layout.Content style={{ minHeight: 'calc(100vh - 52px)' }}>
          <Outlet />
        </Layout.Content>
      </Layout>
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </Layout>
  );
}
