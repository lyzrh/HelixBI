/** 登录页：Premium SaaS 左右分栏。左侧品牌叙事，右侧表单；认证之后一切权限由后端解析。 */

import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import {
  DatabaseOutlined, ExperimentOutlined, LockOutlined, SoundOutlined,
  ThunderboltOutlined, UserOutlined,
} from '@ant-design/icons';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { HelixBadge, HelixMark } from '../components/HelixMark';
import { useAuthStore } from '../stores/authStore';

const CAPABILITIES = [
  { icon: <DatabaseOutlined />, title: '多源数据接入', desc: '文件 / 数据库，归属工作区' },
  { icon: <ThunderboltOutlined />, title: '智能 Agent 分析', desc: '自然语言 → 代码 → 沙箱执行' },
  { icon: <ExperimentOutlined />, title: 'Skill 秒级重放', desc: '分析路径沉淀，越用越快' },
  { icon: <SoundOutlined />, title: '主动洞察', desc: '定时扫描指标突变与异常' },
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

export function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const loading = useAuthStore((s) => s.loading);
  const token = useAuthStore((s) => s.token);
  const [error, setError] = useState('');

  if (token) return <Navigate to="/" replace />;

  const onFinish = async (values: { username: string; password: string }) => {
    setError('');
    try {
      await login(values.username, values.password);
      navigate('/', { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败');
    }
  };

  return (
    <div className="auth-split">
      <AuthSidePanel />
      <div className="auth-main">
        <Card style={{
          width: 400, borderRadius: 16, border: '1px solid #EEF1F6',
          boxShadow: '0 12px 40px rgba(10,21,48,0.10)',
        }} styles={{ body: { padding: '34px 34px 26px' } }}>
          <Typography.Title level={3} style={{ margin: 0 }}>欢迎回来</Typography.Title>
          <Typography.Paragraph type="secondary" style={{ fontSize: 13, margin: '6px 0 20px' }}>
            登录绎数 Helix BI，开启您的数据之旅。
          </Typography.Paragraph>
          {error && <Alert type="error" message={error} style={{ marginBottom: 14 }} showIcon />}
          <Form onFinish={onFinish} layout="vertical" requiredMark={false}>
            <Form.Item name="username" rules={[{ required: true, message: '请输入用户名或邮箱' }]}>
              <Input prefix={<UserOutlined style={{ color: '#98A2B3' }} />}
                placeholder="邮箱 / 用户名" size="large" />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password prefix={<LockOutlined style={{ color: '#98A2B3' }} />}
                placeholder="请输入密码" size="large" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block size="large" loading={loading}
              className="brand-gradient-soft" style={{ border: 'none', fontWeight: 600 }}>
              登 录
            </Button>
          </Form>
          <Typography.Paragraph type="secondary" style={{ fontSize: 13, margin: '18px 0 0', textAlign: 'center' }}>
            还没有账号？<Link to="/register" style={{ fontWeight: 500 }}>立即注册</Link>
          </Typography.Paragraph>
          <div style={{
            marginTop: 18, paddingTop: 14, borderTop: '1px dashed #EEF1F6',
            fontSize: 12, color: '#98A2B3', textAlign: 'center',
          }}>
            内置管理员：admin / admin123（首次登录后请修改密码）
          </div>
        </Card>
      </div>
    </div>
  );
}
