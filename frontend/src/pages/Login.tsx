/** 认证页（登录 / 注册）：Premium SaaS 左右分栏 + 单卡片 Tab 切换。
 * 左侧品牌叙事，右侧表单；认证之后一切权限由后端解析。 */

import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Tabs, Typography } from 'antd';
import {
  DatabaseOutlined, ExperimentOutlined, LockOutlined, MailOutlined,
  SoundOutlined, ThunderboltOutlined, UserOutlined,
} from '@ant-design/icons';
import { Navigate, useNavigate } from 'react-router-dom';
import { HelixBadge, HelixMark } from '../components/HelixMark';
import { api } from '../api/client';
import { useAuthStore } from '../stores/authStore';

const CAPABILITIES = [
  { icon: <DatabaseOutlined />, title: '多源数据接入', desc: 'Connect Data' },
  { icon: <ThunderboltOutlined />, title: '智能 Agent 分析', desc: 'AI Agent' },
  { icon: <ExperimentOutlined />, title: 'Skill 秒级重放', desc: 'Skills' },
  { icon: <SoundOutlined />, title: '洞察与追踪', desc: 'Insights' },
];

export function AuthSidePanel() {
  return (
    <div className="auth-side auth-panel">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <HelixBadge size={40} radius={11} />
        <div>
          <div style={{ fontSize: 19, fontWeight: 700, letterSpacing: 0.2 }}>绎数 Helix BI</div>
          <div className="sider-tagline">Turn Data into Decisions.</div>
        </div>
      </div>
      <div>
        <div style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.42, letterSpacing: -0.5 }}>
          让数据<br />驱动更好的决策
        </div>
        <div style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.55)', marginTop: 12, lineHeight: 1.8 }}>
          AI 驱动的企业级数据分析平台：
          对话式 AgentBI，问一句，得到图表、表格与结论。
        </div>
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, marginTop: 34,
          maxWidth: 460,
        }}>
          {CAPABILITIES.map((c) => (
            <div key={c.title} style={{ display: 'flex', gap: 10 }}>
              <div style={{
                width: 34, height: 34, borderRadius: 9, flexShrink: 0,
                background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.12)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15,
                color: '#C9BBEE',
              }}>{c.icon}</div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{c.title}</div>
                <div style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.45)', marginTop: 2, lineHeight: 1.5 }}>
                  {c.desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.32)', display: 'flex', alignItems: 'center', gap: 6 }}>
        <HelixMark size={13} /> Helix BI · 对话式 AgentBI 数据分析平台
      </div>
    </div>
  );
}

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const login = useAuthStore((s) => s.login);
  const loading = useAuthStore((s) => s.loading);
  const [error, setError] = useState('');

  const onFinish = async (values: { username: string; password: string }) => {
    setError('');
    try {
      await login(values.username, values.password);
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败');
    }
  };

  return (
    <>
      {error && <Alert type="error" message={error} style={{ marginBottom: 14 }} showIcon />}
      <Form onFinish={onFinish} layout="vertical" requiredMark={false}>
        <Form.Item name="username" rules={[{ required: true, message: '请输入用户名或邮箱' }]}>
          <Input prefix={<UserOutlined style={{ color: '#98A2B3' }} />}
            placeholder="请输入邮箱 / 用户名" size="large" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password prefix={<LockOutlined style={{ color: '#98A2B3' }} />}
            placeholder="请输入密码" size="large" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block size="large" loading={loading}
          className="brand-gradient-soft" style={{ border: 'none', fontWeight: 600, marginTop: 4 }}>
          登 录
        </Button>
      </Form>
      <div style={{
        marginTop: 18, paddingTop: 14, borderTop: '1px dashed #EEF1F6',
        fontSize: 12, color: '#98A2B3', textAlign: 'center',
      }}>
        内置管理员：admin / admin123（首次登录后请修改密码）
      </div>
    </>
  );
}

function RegisterForm() {
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const onFinish = async (values: {
    username: string; email: string; password: string; display_name?: string;
  }) => {
    setError('');
    setLoading(true);
    try {
      await api.post('/api/auth/register', {
        username: values.username,
        email: values.email,
        password: values.password,
        display_name: values.display_name || '',
      });
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : '注册失败');
    } finally {
      setLoading(false);
    }
  };

  if (done) {
    return (
      <Alert type="success" showIcon style={{ margin: '4px 0 8px' }}
        message="注册成功"
        description="账号已创建，但尚未加入任何工作区。请联系工作区管理员将您加入工作区并分配角色，然后即可登录。" />
    );
  }

  return (
    <>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, margin: '0 0 14px' }}>
        注册仅创建账号，不授予任何角色；加入工作区后按分配的角色获取权限。
      </Typography.Paragraph>
      {error && <Alert type="error" message={error} style={{ marginBottom: 14 }} showIcon />}
      <Form onFinish={onFinish} layout="vertical" requiredMark={false}>
        <Form.Item name="username"
          rules={[
            { required: true, message: '请输入用户名' },
            { pattern: /^[A-Za-z0-9_.\-]{2,64}$/, message: '2-64 位，仅字母/数字/点/下划线/连字符' },
          ]}>
          <Input prefix={<UserOutlined style={{ color: '#98A2B3' }} />}
            placeholder="用户名" size="large" />
        </Form.Item>
        <Form.Item name="email"
          rules={[
            { required: true, message: '请输入邮箱' },
            { type: 'email', message: '邮箱格式不正确' },
          ]}>
          <Input prefix={<MailOutlined style={{ color: '#98A2B3' }} />}
            placeholder="邮箱" size="large" />
        </Form.Item>
        <Form.Item name="display_name">
          <Input placeholder="昵称（可选）" size="large" maxLength={64} />
        </Form.Item>
        <Form.Item name="password"
          rules={[
            { required: true, message: '请输入密码' },
            { min: 8, message: '密码至少 8 位' },
          ]}>
          <Input.Password prefix={<LockOutlined style={{ color: '#98A2B3' }} />}
            placeholder="密码（至少 8 位）" size="large" />
        </Form.Item>
        <Form.Item name="confirm" dependencies={['password']} rules={[
          { required: true, message: '请再次输入密码' },
          ({ getFieldValue }) => ({
            validator: (_, v) =>
              !v || v === getFieldValue('password')
                ? Promise.resolve()
                : Promise.reject(new Error('两次输入的密码不一致')),
          }),
        ]}>
          <Input.Password prefix={<LockOutlined style={{ color: '#98A2B3' }} />}
            placeholder="确认密码" size="large" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block size="large" loading={loading}
          className="brand-gradient-soft" style={{ border: 'none', fontWeight: 600, marginTop: 4 }}>
          注 册
        </Button>
      </Form>
    </>
  );
}

export function AuthScreen({ initialTab = 'login' }: { initialTab?: 'login' | 'register' }) {
  const navigate = useNavigate();
  const token = useAuthStore((s) => s.token);

  if (token) return <Navigate to="/" replace />;

  return (
    <div className="auth-split">
      <AuthSidePanel />
      <div className="auth-main">
        <Card style={{
          width: 410, borderRadius: 16, border: '1px solid #EEF1F6',
          boxShadow: '0 12px 40px rgba(10,21,48,0.10)',
        }} styles={{ body: { padding: '26px 34px 24px' } }}>
          <Tabs
            defaultActiveKey={initialTab}
            centered
            items={[
              {
                key: 'login', label: <span style={{ fontWeight: 600 }}>登 录</span>,
                children: (
                  <>
                    <Typography.Title level={3} style={{ margin: '2px 0 4px', textAlign: 'center' }}>
                      欢迎回来
                    </Typography.Title>
                    <Typography.Paragraph type="secondary"
                      style={{ fontSize: 13, margin: '0 0 20px', textAlign: 'center' }}>
                      登入绎数 HelixBI，开启您的数据之旅
                    </Typography.Paragraph>
                    <LoginForm onSuccess={() => navigate('/', { replace: true })} />
                  </>
                ),
              },
              {
                key: 'register', label: <span style={{ fontWeight: 600 }}>注 册</span>,
                children: <RegisterForm />,
              },
            ]}
          />
        </Card>
      </div>
    </div>
  );
}

export function Login() {
  return <AuthScreen initialTab="login" />;
}
