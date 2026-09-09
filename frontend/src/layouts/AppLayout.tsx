import { Avatar, Layout, Menu, Tag, Tooltip } from 'antd';
import {
  DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  MessageOutlined, RocketOutlined, SettingOutlined, SoundOutlined,
  ThunderboltOutlined, PieChartOutlined,
} from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { SettingsModal } from '../components/SettingsModal';
import { useAppStore } from '../stores/appStore';

const NAV = [
  { key: '/', icon: <RocketOutlined />, label: '工作台' },
  { key: '/chat', icon: <MessageOutlined />, label: '对话分析' },
  { key: '/explore', icon: <PieChartOutlined />, label: '自助分析' },
  { key: '/agents', icon: <ThunderboltOutlined />, label: '场景 Agent' },
  { key: '/skills', icon: <ExperimentOutlined />, label: 'Skill 库' },
  { key: '/insights', icon: <SoundOutlined />, label: '主动洞察' },
  { key: '/dashboards', icon: <DashboardOutlined />, label: '仪表板' },
  { key: '/datasources', icon: <DatabaseOutlined />, label: '数据源' },
];

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const health = useAppStore((s) => s.health);
  const profile = useAppStore((s) => s.profile);
  const loadProfile = useAppStore((s) => s.loadProfile);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => { loadProfile(); }, [loadProfile]);

  const navKey = location.pathname.startsWith('/chat') ? '/chat' : location.pathname;
  const selected = NAV.find((n) => n.key === navKey);
  const pageTitle = selected?.label ?? '绎数';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider width={208} theme="dark"
        style={{
          position: 'fixed', left: 0, top: 0, bottom: 0, overflow: 'auto',
          background: 'linear-gradient(180deg,#0b1a38 0%,#0f1e3d 60%,#12233f 100%)',
        }}>
        <div style={{ padding: '20px 16px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: 'linear-gradient(135deg,#3b82f6,#8b5cf6)',
            color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 16, fontWeight: 700,
          }}>绎</div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, color: '#fff' }}>绎数</div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)' }}>Helix BI · 推演数据 洞见本质</div>
          </div>
        </div>
        <Menu mode="inline" theme="dark" selectedKeys={[navKey]} items={NAV}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', padding: '0 8px', background: 'transparent' }} />
        <div style={{ position: 'absolute', bottom: 16, left: 16, right: 16 }}>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span>
              沙箱：
              {health?.sandbox
                ? <Tag color="success" style={{ marginLeft: 4 }}>在线</Tag>
                : <Tag color="warning" style={{ marginLeft: 4 }}>离线</Tag>}
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              模型：
              <Tooltip title="点击配置 LLM 接口">
                <Tag
                  color={health?.llm_configured ? 'processing' : 'error'}
                  style={{ marginLeft: 4, cursor: 'pointer' }}
                  onClick={() => setSettingsOpen(true)}
                >
                  {health?.llm_configured ? (health.model || '已配置') : '未配置'}
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
          <Tooltip title="设置中心（LLM 接口 / 偏好设置 / 个人资料）">
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
