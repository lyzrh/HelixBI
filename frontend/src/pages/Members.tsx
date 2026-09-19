/** 成员管理页：仅 member:manage 权限可见（后端同样强制校验，前端隐藏只是 UX）。 */

import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Typography, message } from 'antd';
import { PlusOutlined, UserAddOutlined } from '@ant-design/icons';
import { api } from '../api/client';
import { useAuthStore } from '../stores/authStore';

interface MemberRow {
  id: number;
  workspace_id: number;
  user_id: number;
  role_code: string;
  created_at: string;
  username: string;
  display_name: string;
  email: string | null;
}

interface UserRow {
  id: number;
  username: string;
  email: string | null;
  display_name: string;
}

const ROLE_COLORS: Record<string, string> = {
  admin: 'red', analyst: 'purple', viewer: 'blue',
};

const ROLE_LABEL: CSSProperties = {
  fontSize: 12.5, fontWeight: 600, borderRadius: 6, padding: '2px 8px', border: '1px solid',
};

export function Members() {
  const context = useAuthStore((s) => s.context);
  const canManage = !!context?.permissions.includes('member:manage');
  const wsId = context?.workspace_id;

  const [members, setMembers] = useState<MemberRow[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    if (!wsId || !canManage) return;
    setLoading(true);
    try {
      const rows = await api.get<MemberRow[]>(`/api/auth/workspaces/${wsId}/members`);
      setMembers(rows);
    } finally {
      setLoading(false);
    }
  }, [wsId, canManage]);

  useEffect(() => { load(); }, [load]);

  // 未授权（或直敲 URL 进入）时只给提示；真正的防线在后端 403
  if (!canManage) {
    return (
      <Card style={{ margin: 24 }}>
        <Typography.Text type="secondary">
          当前角色（{context?.role ?? '未知'}）没有成员管理权限。如需管理成员，请联系工作区管理员。
        </Typography.Text>
      </Card>
    );
  }

  const openAdd = async () => {
    form.resetFields();
    setAddOpen(true);
    try {
      // workspace:manage 权限（admin 独有）才能列出平台用户用于选择
      setUsers(await api.get<UserRow[]>('/api/auth/users'));
    } catch { /* 403 已由 client 提示 */ }
  };

  const submitAdd = async () => {
    const values = await form.validateFields();
    await api.post(`/api/auth/workspaces/${wsId}/members`, values);
    message.success('成员已加入工作区');
    setAddOpen(false);
    load();
  };

  const changeRole = async (uid: number, roleCode: string) => {
    await api.patch(`/api/auth/workspaces/${wsId}/members/${uid}`, { role_code: roleCode });
    message.success('角色已更新');
    load();
  };

  const removeMember = async (row: MemberRow) => {
    await api.del(`/api/auth/workspaces/${wsId}/members/${row.user_id}`);
    message.success(`已移除 ${row.username}`);
    load();
  };

  const membersByUser = new Set(members.map((m) => m.username));

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 19, fontWeight: 700, color: '#101828' }}>工作区成员</div>
        <div style={{ fontSize: 13, color: '#475467', marginTop: 3 }}>
          管理当前工作区的成员与角色；角色决定权限集合（User → Membership → Role → Permission）。
        </div>
      </div>
      <Card title={null}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}
            className="brand-gradient-soft" style={{ border: 'none' }}>
            添加成员
          </Button>
        }
        style={{ borderRadius: 14 }} styles={{ body: { paddingTop: 4 } }}>
        <div style={{ fontWeight: 600, fontSize: 15, padding: '6px 0 10px' }}>成员列表</div>
        <Table<MemberRow>
          rowKey="id"
          loading={loading}
          dataSource={members}
          pagination={false}
          columns={[
            { title: '用户', dataIndex: 'username',
              render: (_, r) => (
                <Space size={6}>
                  <UserAddOutlined style={{ color: '#5645D4' }} />
                  <span>{r.username}</span>
                  {r.display_name && r.display_name !== r.username && (
                    <Typography.Text type="secondary">（{r.display_name}）</Typography.Text>
                  )}
                </Space>
              ) },
            { title: '邮箱', dataIndex: 'email',
              render: (v) => v || <Typography.Text type="secondary">—</Typography.Text> },
            { title: '角色', dataIndex: 'role_code', width: 200,
              render: (role: string, r) => (
                <Select size="small" value={role} style={{ width: 130 }}
                  onChange={(v) => changeRole(r.user_id, v)}
                  options={Object.keys(ROLE_COLORS).map((c) => ({ value: c, label: c }))} />
              ) },
            { title: '加入时间', dataIndex: 'created_at', width: 180 },
            { title: '操作', width: 120,
              render: (_, r) => (
                <Popconfirm title={`确定移除 ${r.username}？`}
                  description="移除后该用户立即失去本工作区的全部权限。"
                  onConfirm={() => removeMember(r)}>
                  <Button size="small" danger>移除</Button>
                </Popconfirm>
              ) },
          ]} />
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 14 }}>
          角色：admin（全部权限，含成员管理）/ analyst（分析、SQL、数据源、Skill）/ viewer（只读）。
          工作区最后一名管理员不能被移除或降级。
        </Typography.Paragraph>
      </Card>

      <Modal title="添加成员" open={addOpen} onOk={submitAdd}
        onCancel={() => setAddOpen(false)} okText="加入工作区" cancelText="取消">
        <Form form={form} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入已注册用户的用户名' }]}>
            <Input placeholder="已注册用户的用户名" list="helix-registered-users" />
          </Form.Item>
          <datalist id="helix-registered-users">
            {users.filter((u) => !membersByUser.has(u.username)).map((u) => (
              <option key={u.id} value={u.username}>{u.display_name}{u.email ? ` · ${u.email}` : ''}</option>
            ))}
          </datalist>
          <Form.Item name="role_code" label="角色" initialValue="viewer" rules={[{ required: true }]}>
            <Select options={[
              { value: 'viewer', label: <span className="role-tag-viewer" style={ROLE_LABEL}>viewer · 查看者（只读）</span> },
              { value: 'analyst', label: <span className="role-tag-analyst" style={ROLE_LABEL}>analyst · 分析师（可执行分析）</span> },
              { value: 'admin', label: <span className="role-tag-admin" style={ROLE_LABEL}>admin · 管理员（全部权限）</span> },
            ]} />
          </Form.Item>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            只能添加已完成注册的用户；新用户可在登录页点击「注册」创建账号。
          </Typography.Paragraph>
        </Form>
      </Modal>
    </div>
  );
}
