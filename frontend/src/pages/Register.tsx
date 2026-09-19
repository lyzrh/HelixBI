/** 注册页：Premium SaaS 左右分栏（与登录页共用品牌面板）。仅创建认证身份，角色由管理员分配。 */

import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { LockOutlined, MailOutlined, UserOutlined } from '@ant-design/icons';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { AuthSidePanel } from './Login';
import { api } from '../api/client';
import { useAuthStore } from '../stores/authStore';

interface RegisterResp {
  id: number;
  username: string;
}

export function Register() {
  const navigate = useNavigate();
  const token = useAuthStore((s) => s.token);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  if (token) return <Navigate to="/" replace />;

  const onFinish = async (values: {
    username: string; email: string; password: string; display_name?: string;
  }) => {
    setError('');
    setLoading(true);
    try {
      await api.post<RegisterResp>('/api/auth/register', {
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

  return (
    <div className="auth-split">
      <AuthSidePanel />
      <div className="auth-main">
        <Card style={{
          width: 400, borderRadius: 16, border: '1px solid #EEF1F6',
          boxShadow: '0 12px 40px rgba(10,21,48,0.10)',
        }} styles={{ body: { padding: '34px 34px 26px' } }}>
          <Typography.Title level={3} style={{ margin: 0 }}>创建账号</Typography.Title>
          <Typography.Paragraph type="secondary" style={{ fontSize: 13, margin: '6px 0 20px' }}>
            注册后请联系管理员加入工作区；权限由管理员分配，注册不授予任何角色。
          </Typography.Paragraph>
          {done ? (
            <>
              <Alert type="success" showIcon style={{ margin: '8px 0 18px' }}
                message="注册成功"
                description="账号已创建，但尚未加入任何工作区。请联系工作区管理员将您加入工作区并分配角色，然后即可登录。" />
              <Button type="primary" block size="large" className="brand-gradient-soft"
                style={{ border: 'none', fontWeight: 600 }}
                onClick={() => navigate('/login', { replace: true })}>
                返回登录
              </Button>
            </>
          ) : (
            <>
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
                  className="brand-gradient-soft" style={{ border: 'none', fontWeight: 600 }}>
                  注 册
                </Button>
              </Form>
              <Typography.Paragraph type="secondary" style={{ fontSize: 13, margin: '18px 0 0', textAlign: 'center' }}>
                已有账号？<Link to="/login" style={{ fontWeight: 500 }}>直接登录</Link>
              </Typography.Paragraph>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
