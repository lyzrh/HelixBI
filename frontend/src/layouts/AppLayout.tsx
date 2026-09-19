import { Avatar, Dropdown, Layout, Menu, Select, Space, Tag, Tooltip } from 'antd';
import {
  DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  LogoutOutlined, MessageOutlined, RocketOutlined, SettingOutlined, SoundOutlined,
  TeamOutlined, ThunderboltOutlined, PieChartOutlined, SwapOutlined,
} from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { SettingsModal } from '../components/SettingsModal';
import { HelixBadge } from '../components/HelixMark';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';
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
  // 成员管理仅 admin（member:manage 权限）可见；权限由后端强制校验，前端隐藏只是 UX
  { key: '/members', icon: <TeamOutlined />, labelKey: 'nav.members' as StrKey, permission: 'member:manage' },
];

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const t = useT();
  const health = useAppStore((s) => s.health);
  const profile = useAppStore((s) => s.profile);
  const loadProfile = useAppStore((s) => s.loadProfile);
  const context = useAuthStore((s) => s.context);
  const workspaces = useAuthStore((s) => s.workspaces);
  const switchWorkspace = useAuthStore((s) => s.switchWorkspace);
  const logout = useAuthStore((s) => s.logout);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => { loadProfile(); }, [loadProfile]);

  const onSwitchWorkspace = async (wsId: number) => {
    if (!context || wsId === context.workspace_id) return;
    // 切换工作区 → 重新获取 UserContext（角色/权限随新工作区变化）→ 刷新整站
    await switchWorkspace(wsId);
    window.location.href = '/';
  };

  const userMenu = {
    items: [
      { key: 'who', type: 'group' as const,
        label: `${context?.username ?? profile.nickname} · ${context?.role ?? ''}` },
      { type: 'divider' as const },
      { key: 'switch', icon: <SwapOutlined />, label: '切换工作区',
        children: workspaces.map((w) => ({
          key: `ws-${w.workspace_id}`,
          label: `${w.name}（${w.role_code}）`,
        })) },
      { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
      { type: 'divider' as const },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录' },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === 'logout') {
        logout();
        navigate('/login', { replace: true });
      } else if (key === 'settings') {
        setSettingsOpen(true);
      } else if (key.startsWith('ws-')) {
        onSwitchWorkspace(Number(key.slice(3)));
      }
    },
  };

  const navKey = location.pathname.startsWith('/chat') ? '/chat' : location.pathname;
  const selected = NAV.find((n) => n.key === navKey);
  const pageTitle = selected ? t(selected.labelKey) : 'Helix BI';

  const menuItems = NAV
    .filter((n) => !('permission' in n) || !!context?.permissions.includes(n.permission!))
    .map((n) => ({ key: n.key, icon: n.icon, label: t(n.labelKey) }));

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider width={208} theme="dark"
        style={{
          position: 'fixed', left: 0, top: 0, bottom: 0, overflow: 'auto',
          background: '#0A1530',
        }}>
        <div style={{ padding: '20px 16px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <HelixBadge size={36} radius={10} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, color: '#fff', letterSpacing: 0.3 }}>绎数 Helix BI</div>
            <div className="sider-tagline">Turn Data into Decisions.</div>
          </div>
        </div>
        <div style={{ height: 1, background: '#1A2A52', margin: '0 16px 8px' }} />
        <Menu mode="inline" theme="dark" selectedKeys={[navKey]} items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', padding: '0 8px', background: 'transparent' }} />
        <div style={{ position: 'absolute', bottom: 16, left: 16, right: 16 }}>
          {/* 当前工作区：切换后重新获取 UserContext（权限控制仍由后端完成） */}
          {context && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.38)', padding: '0 2px 5px' }}>
                当前工作区
              </div>
              <Select
                size="small"
                value={context.workspace_id}
                onChange={(v) => onSwitchWorkspace(v)}
                style={{ width: '100%' }}
                popupMatchSelectWidth={false}
                options={workspaces.map((w) => ({
                  value: w.workspace_id,
                  label: (
                    <Space size={6}>
                      <DatabaseOutlined style={{ color: '#5645D4' }} />
                      <span style={{ fontSize: 12 }}>{w.name}</span>
                      <span className={`role-tag-${w.role_code}`} style={{
                        fontSize: 10, fontWeight: 600, borderRadius: 5, marginLeft: 'auto',
                        padding: '0 5px', lineHeight: '15px', border: '1px solid',
                      }}>{w.role_code}</span>
                    </Space>
                  ),
                }))}
              />
            </div>
          )}
          <div style={{
            fontSize: 12, color: '#A4A097', display: 'flex', flexDirection: 'column', gap: 6,
            background: 'rgba(255,255,255,0.04)', borderRadius: 12, padding: '10px 12px',
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
          {/* 当前用户卡：企业级身份元素（头像 + 用户名 + 实时角色） */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 9, marginTop: 10,
            background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 12, padding: '8px 12px',
          }}>
            <Avatar size={28} style={{
              background: 'linear-gradient(135deg,#5645D4,#7B5CF5)',
              fontWeight: 600, fontSize: 13, flexShrink: 0,
            }}>
              {(context?.username || 'U').slice(0, 1).toUpperCase()}
            </Avatar>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ color: '#fff', fontSize: 12.5, fontWeight: 600,
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {context?.username || profile.nickname}
              </div>
              {context?.role && (
                <span className={`role-tag-${context.role}`} style={{
                  fontSize: 10.5, fontWeight: 600, borderRadius: 5, border: '1px solid',
                  padding: '0 6px', lineHeight: '15px', display: 'inline-block', marginTop: 2,
                }}>{context.role}</span>
              )}
            </div>
            <LogoutOutlined style={{ color: 'rgba(255,255,255,0.45)', fontSize: 13, cursor: 'pointer' }}
              onClick={() => { logout(); navigate('/login', { replace: true }); }} />
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
          <Dropdown menu={userMenu} trigger={['click']}>
            <Tooltip title={`${profile.nickname} · ${profile.role}`}>
              <div
                style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '0 2px' }}
              >
                <Avatar size={32} style={{
                  background: 'linear-gradient(135deg,#5645D4,#7B5CF5)',
                  fontWeight: 600, fontSize: 14,
                }}>
                  {(context?.username || profile.nickname || 'U').slice(0, 1).toUpperCase()}
                </Avatar>
                <span style={{ fontSize: 13, color: '#334155' }}>
                  {context?.username || profile.nickname}
                </span>
                {context?.role && (
                  <span className={`role-tag-${context.role}`} style={{
                    fontSize: 11, fontWeight: 600, borderRadius: 6,
                    padding: '1px 8px', lineHeight: '18px', border: '1px solid',
                  }}>{context.role}</span>
                )}
              </div>
            </Tooltip>
          </Dropdown>
        </Layout.Header>
        <Layout.Content style={{ minHeight: 'calc(100vh - 52px)' }}>
          <Outlet />
        </Layout.Content>
      </Layout>
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </Layout>
  );
}
