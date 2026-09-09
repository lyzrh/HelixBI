/** 场景 Agent 页：卡片列表 + 新建/编辑 + 启停 + 开聊入口 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  App as AntApp, Button, Card, Col, Empty, Input, Modal, Popconfirm,
  Row, Select, Space, Switch, Tag,
} from 'antd';
import {
  BuildOutlined, EditOutlined, LineChartOutlined, MessageOutlined,
  PlusOutlined, RobotOutlined, ShopOutlined, ShoppingCartOutlined, ToolOutlined,
} from '@ant-design/icons';
import { api } from '../api/client';
import type { SceneAgentInfo } from '../api/types';
import { useAppStore } from '../stores/appStore';

const ICONS: Record<string, React.ReactNode> = {
  robot: <RobotOutlined />,
  'shopping-cart': <ShoppingCartOutlined />,
  tool: <ToolOutlined />,
  factory: <BuildOutlined />,
  shop: <ShopOutlined />,
  chart: <LineChartOutlined />,
};

const PRESET_COLORS = ['#2563eb', '#16a34a', '#7c3aed', '#ea580c', '#0d9488', '#dc2626'];

interface AgentForm {
  name: string; description: string; pack_id: string;
  data_source_ids: number[]; intro: string;
  recommended_questions: string[]; icon: string; color: string;
}

const emptyForm: AgentForm = {
  name: '', description: '', pack_id: '', data_source_ids: [], intro: '',
  recommended_questions: [], icon: 'robot', color: '#2563eb',
};

export function Agents() {
  const navigate = useNavigate();
  const { message } = AntApp.useApp();

  const [agents, setAgents] = useState<SceneAgentInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const dataSources = useAppStore((s) => s.dataSources);
  const packs = useAppStore((s) => s.packs);
  const loadDataSources = useAppStore((s) => s.loadDataSources);
  const loadPacks = useAppStore((s) => s.loadPacks);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<SceneAgentInfo | null>(null); // null = 新建
  const [form, setForm] = useState<AgentForm>(emptyForm);
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      setAgents(await api.get<SceneAgentInfo[]>('/api/agents'));
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
    loadDataSources();
    loadPacks();
  }, [loadDataSources, loadPacks]);

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyForm, pack_id: packs[0]?.id ?? '' });
    setModalOpen(true);
  };

  const openEdit = (a: SceneAgentInfo) => {
    setEditing(a);
    setForm({
      name: a.name, description: a.description, pack_id: a.pack_id,
      data_source_ids: a.data_source_ids || [], intro: a.intro,
      recommended_questions: a.recommended_questions || [],
      icon: a.icon, color: a.color,
    });
    setModalOpen(true);
  };

  const save = async () => {
    if (!form.name.trim()) { message.warning('请填写名称'); return; }
    if (!form.pack_id) { message.warning('请选择语义包'); return; }
    setSaving(true);
    try {
      const body = {
        name: form.name.trim(), description: form.description,
        pack_id: form.pack_id, data_source_ids: form.data_source_ids,
        intro: form.intro, recommended_questions: form.recommended_questions,
        icon: form.icon, color: form.color,
      };
      if (editing) {
        await api.patch(`/api/agents/${editing.id}`, body);
      } else {
        await api.post('/api/agents', body);
      }
      message.success(editing ? '已保存' : '已创建');
      setModalOpen(false);
      reload();
    } catch { /* client.ts 已提示 */ } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (a: SceneAgentInfo, enabled: boolean) => {
    try {
      await api.patch(`/api/agents/${a.id}`, { enabled });
      setAgents((list) => list.map((x) => (x.id === a.id ? { ...x, enabled } : x)));
    } catch { /* client.ts 已提示 */ }
  };

  const remove = async (a: SceneAgentInfo) => {
    try {
      await api.del(`/api/agents/${a.id}`);
      message.success('已删除');
      reload();
    } catch { /* client.ts 已提示 */ }
  };

  const startChat = async (a: SceneAgentInfo) => {
    try {
      const s = await api.post<{ id: number }>('/api/sessions', { agent_id: a.id });
      navigate(`/chat/${s.id}`);
    } catch { /* client.ts 已提示 */ }
  };

  const dsName = (id: number) => dataSources.find((d) => d.id === id)?.name ?? `#${id}`;

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>场景 Agent</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            预置行业分析专家：绑定语义包与数据源，带推荐问题，开箱即用
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建 Agent</Button>
      </div>

      <Row gutter={[14, 14]}>
        {agents.map((a) => (
          <Col key={a.id} xs={24} sm={12} lg={8}>
            <Card
              loading={loading}
              style={{
                borderRadius: 12, opacity: a.enabled ? 1 : 0.6,
                borderTop: `3px solid ${a.color}`,
              }}
              styles={{ body: { padding: '16px 18px' } }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <div style={{
                  width: 44, height: 44, borderRadius: 10, flexShrink: 0,
                  background: `${a.color}14`, color: a.color,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22,
                }}>{ICONS[a.icon] ?? <RobotOutlined />}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>{a.name}</span>
                    {a.builtin && <Tag style={{ fontSize: 11 }}>内置</Tag>}
                    {!a.enabled && <Tag color="default" style={{ fontSize: 11 }}>已停用</Tag>}
                  </div>
                  <div style={{ fontSize: 12.5, color: '#64748b', marginTop: 2 }}>
                    {packs.find((p) => p.id === a.pack_id)?.name ?? a.pack_id}
                  </div>
                </div>
                <Switch size="small" checked={a.enabled} onChange={(v) => toggleEnabled(a, v)} />
              </div>

              <div style={{ fontSize: 13, color: '#475569', margin: '10px 0 8px', minHeight: 20 }}>
                {a.description || '（无描述）'}
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
                {(a.data_source_ids || []).map((id) => (
                  <Tag key={id} color="blue" style={{ fontSize: 11.5 }}>{dsName(id)}</Tag>
                ))}
                {!a.data_source_ids?.length && (
                  <Tag color="warning" style={{ fontSize: 11.5 }}>未绑定数据源</Tag>
                )}
              </div>

              {!!a.recommended_questions?.length && (
                <div style={{ margin: '8px 0 4px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {a.recommended_questions.slice(0, 3).map((q) => (
                    <span key={q} className="followup-pill" style={{ fontSize: 12, padding: '3px 10px' }}
                      onClick={() => startChat(a)}>{q}</span>
                  ))}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <Button type="primary" size="small" icon={<MessageOutlined />}
                  disabled={!a.enabled} onClick={() => startChat(a)}>开始分析</Button>
                <div style={{ flex: 1 }} />
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(a)}>编辑</Button>
                {!a.builtin && (
                  <Popconfirm title={`删除场景 Agent「${a.name}」？`} onConfirm={() => remove(a)}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                )}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {!loading && !agents.length && (
        <Empty description="暂无场景 Agent，点击右上角新建" image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ marginTop: 60 }} />
      )}

      <Modal
        open={modalOpen}
        title={editing ? `编辑 Agent · ${editing.name}` : '新建场景 Agent'}
        width={620}
        onOk={save} onCancel={() => setModalOpen(false)}
        okText="保存" cancelText="取消"
        confirmLoading={saving}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 8 }}>
          <div>
            <div style={labelStyle}>名称</div>
            <Input value={form.name} maxLength={40} placeholder="如：门店运营分析"
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </div>
          <div>
            <div style={labelStyle}>描述</div>
            <Input value={form.description} maxLength={100} placeholder="一句话说明用途"
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div>
            <div style={labelStyle}>语义包（决定分析口径与指标定义）</div>
            <Select style={{ width: '100%' }} value={form.pack_id || undefined}
              onChange={(v) => setForm((f) => ({ ...f, pack_id: v }))}
              options={packs.map((p) => ({ value: p.id, label: p.name }))} />
          </div>
          <div>
            <div style={labelStyle}>绑定数据源（可多选）</div>
            <Select mode="multiple" style={{ width: '100%' }} placeholder="选择数据源"
              value={form.data_source_ids} maxTagCount="responsive"
              onChange={(v) => setForm((f) => ({ ...f, data_source_ids: v }))}
              options={dataSources.map((d) => ({ value: d.id, label: d.name }))} />
          </div>
          <div>
            <div style={labelStyle}>开场白（进入对话时展示）</div>
            <Input.TextArea rows={2} maxLength={300} value={form.intro}
              onChange={(e) => setForm((f) => ({ ...f, intro: e.target.value }))} />
          </div>
          <div>
            <div style={labelStyle}>推荐问题（回车添加）</div>
            <Select mode="tags" style={{ width: '100%' }} placeholder="输入问题后回车"
              value={form.recommended_questions}
              onChange={(v) => setForm((f) => ({ ...f, recommended_questions: v }))}
              tokenSeparators={['\n']} />
          </div>
          <div style={{ display: 'flex', gap: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={labelStyle}>图标</div>
              <Select style={{ width: '100%' }} value={form.icon}
                onChange={(v) => setForm((f) => ({ ...f, icon: v }))}
                optionRender={(o) => <Space>{ICONS[o.value as string]}{o.label}</Space>}
                options={Object.keys(ICONS).map((k) => ({ value: k, label: k }))} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={labelStyle}>主题色</div>
              <div style={{ display: 'flex', gap: 8, paddingTop: 4 }}>
                {PRESET_COLORS.map((c) => (
                  <span key={c} onClick={() => setForm((f) => ({ ...f, color: c }))}
                    style={{
                      width: 24, height: 24, borderRadius: 6, background: c, cursor: 'pointer',
                      outline: form.color === c ? '2px solid #334155' : 'none',
                      outlineOffset: 2,
                    }} />
                ))}
              </div>
            </div>
          </div>
        </Space>
      </Modal>
    </div>
  );
}

const labelStyle: React.CSSProperties = { fontSize: 12.5, color: '#64748b', marginBottom: 4 };
