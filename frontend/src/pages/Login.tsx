/** 登录页：用户名/邮箱 + 口令。认证之后的一切权限由后端解析。 */

import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Navigate, useNavigate } from 'react-router-dom';
import { HelixBadge } from '../components/HelixMark';
import { useAuthStore } from '../stores/authStore';

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
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #0A1530 0%, #1A2A52 55%, #5645D4 130%)',
    }}>
      <Card style={{ width: 380, borderRadius: 14, boxShadow: '0 18px 50px rgba(10,21,48,0.45)' }}
        styles={{ body: { padding: '30px 30px 22px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <HelixBadge size={40} />
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>绎数 Helix BI</Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              对话式 AgentBI 数据分析平台
            </Typography.Text>
          </div>
        </div>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '4px 0 16px' }}>
          登录后进入工作区；角色与权限由管理员分配。
        </Typography.Paragraph>
        {error && <Alert type="error" message={error} style={{ marginBottom: 14 }} showIcon />}
        <Form onFinish={onFinish} layout="vertical" requiredMark={false}>
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名或邮箱' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名 / 邮箱" size="large" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}
            style={{ background: '#5645D4' }}>
            登 录
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '14px 0 0' }}>
          内置管理员：admin / admin123
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
