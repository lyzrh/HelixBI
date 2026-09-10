/** 对话分析页：会话侧栏 + 消息流 + 输入区 + spec 确认 / 固定仪表板 / 沉淀 Skill 弹窗 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  App as AntApp, Button, Input, Modal, Popconfirm, Select,
  Space, Switch, Tag, Tooltip,
} from 'antd';
import {
  CheckSquareOutlined, DatabaseOutlined, DeleteOutlined, EditOutlined, PlusOutlined, SendOutlined,
} from '@ant-design/icons';
import { api } from '../api/client';
import type { DashboardInfo, QuerySpec, SessionInfo } from '../api/types';
import { MessageItem, StreamingItem, type MessageCallbacks } from '../components/chat/MessageItem';
import { OperationFlow } from '../components/OperationFlow';
import { useAppStore } from '../stores/appStore';
import { useChatStore } from '../stores/chatStore';

const GENERIC_QUESTIONS = [
  '各维度的核心指标汇总，按从高到低排序',
  '近 30 天关键指标趋势，并计算环比',
  '找出表现最差的 Top 5，给出原因线索',
];

export function Chat() {
  const navigate = useNavigate();
  const { sessionId } = useParams();
  const { message } = AntApp.useApp();

  const {
    sessions, currentSession, messages, streaming, selectedDsIds, confirmSpec,
    loadSessions, openSession, createSession, deleteSession,
    setSelectedDsIds, setConfirmSpec, ask, rerun,
  } = useChatStore();
  const dataSources = useAppStore((s) => s.dataSources);
  const loadDataSources = useAppStore((s) => s.loadDataSources);

  const [input, setInput] = useState('');
  const [confirmMode, setConfirmMode] = useState(false);
  const [specText, setSpecText] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  // 固定到仪表板
  const [pin, setPin] = useState<{
    type: 'chart' | 'table' | 'text'; title: string;
    payload: Record<string, unknown>; runId?: number;
  } | null>(null);
  const [dashboards, setDashboards] = useState<DashboardInfo[]>([]);
  const [dashChoice, setDashChoice] = useState<number | 'new'>(-1);
  const [newDashName, setNewDashName] = useState('');
  // 沉淀 Skill
  const [skillOf, setSkillOf] = useState<number | null>(null);
  const [skillForm, setSkillForm] = useState({ name: '', description: '', tags: [] as string[] });
  // 会话重命名
  const [renameTarget, setRenameTarget] = useState<SessionInfo | null>(null);
  const [renameText, setRenameText] = useState('');

  const doRename = async () => {
    if (!renameTarget) return;
    const title = renameText.trim();
    if (!title) { message.warning('名称不能为空'); return; }
    try {
      await api.patch(`/api/sessions/${renameTarget.id}`, { title });
      useChatStore.setState({
        sessions: sessions.map((s) => (s.id === renameTarget.id ? { ...s, title } : s)),
        currentSession: currentSession?.id === renameTarget.id
          ? { ...currentSession, title } : currentSession,
      });
      setRenameTarget(null);
    } catch { /* client.ts 已提示 */ }
  };

  useEffect(() => {
    loadSessions();
    loadDataSources();
  }, [loadSessions, loadDataSources]);

  // 路由参数驱动会话切换
  useEffect(() => {
    const id = sessionId ? Number(sessionId) : null;
    if (id && id !== currentSession?.id) openSession(id);
    if (!id && currentSession) useChatStore.setState({ currentSession: null, messages: [], streaming: null });
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 新消息 / 流式更新时滚动到底部
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  const busy = !!streaming?.running;

  const doAsk = useCallback(async (q: string, spec?: Record<string, unknown> | null) => {
    if (busy) { message.warning('分析进行中，请稍候'); return; }
    if (!currentSession) { message.warning('请先创建会话'); return; }
    if (!selectedDsIds.length) { message.warning('请先选择至少一个数据源'); return; }
    await ask(q, spec);
  }, [busy, currentSession, selectedDsIds, ask, message]);

  /** 发送：确认模式下先 parse 出 spec 供人工确认 */
  const onSend = async () => {
    const q = input.trim();
    if (!q || busy) return;
    if (!currentSession) { message.warning('请先创建会话'); return; }
    if (!selectedDsIds.length) { message.warning('请先选择至少一个数据源'); return; }
    if (!confirmMode) {
      setInput('');
      await doAsk(q);
      return;
    }
    try {
      const r = await api.post<{ spec: QuerySpec; error?: string }>(
        `/api/sessions/${currentSession.id}/parse`,
        { question: q, data_source_ids: selectedDsIds },
      );
      if (r.error) {
        message.warning(`意图解析失败：${r.error}，将直接执行`);
        setInput('');
        await doAsk(q);
        return;
      }
      setConfirmSpec({ question: q, spec: r.spec as Record<string, unknown> });
      setSpecText(JSON.stringify(r.spec, null, 2));
      setInput('');
    } catch { /* client.ts 已提示 */ }
  };

  const confirmSpecOk = async () => {
    if (!confirmSpec) return;
    let spec: Record<string, unknown> | null = confirmSpec.spec;
    try {
      spec = JSON.parse(specText);
    } catch {
      message.warning('spec JSON 格式有误，已按原解析结果执行');
    }
    setConfirmSpec(null);
    await doAsk(confirmSpec.question, spec);
  };

  const cb: MessageCallbacks = {
    onAsk: (q) => doAsk(q),
    onRerun: (runId) => rerun(runId),
    onCaptureSkill: (runId) => {
      const m = messages.find((x) => x.meta?.run_id === runId && x.role === 'user');
      setSkillForm({
        name: (m?.content || `Skill-${runId}`).slice(0, 30),
        description: '', tags: [],
      });
      setSkillOf(runId);
    },
    onPinChart: (url, runId) => setPin({
      type: 'chart', title: `图表 · ${new Date().toLocaleDateString()}`,
      payload: { chart_url: url }, runId,
    }),
    onPinTable: (name, rows, runId) => setPin({
      type: 'table', title: `表格 · ${name}`,
      payload: { table_name: name, rows: rows.slice(0, 500) }, runId,
    }),
    onPinText: (text, runId) => setPin({
      type: 'text', title: '分析结论',
      payload: { text }, runId,
    }),
  };

  // 打开固定弹窗时加载仪表板列表
  useEffect(() => {
    if (pin) {
      api.get<DashboardInfo[]>('/api/dashboards').then((list) => {
        setDashboards(list);
        setDashChoice(list.length ? list[0].id : 'new');
        setNewDashName('');
      });
    }
  }, [pin]);

  const pinOk = async () => {
    if (!pin) return;
    try {
      let did = dashChoice;
      if (did === 'new') {
        if (!newDashName.trim()) { message.warning('请填写新仪表板名称'); return; }
        const d = await api.post<DashboardInfo>('/api/dashboards', { name: newDashName.trim(), description: '' });
        did = d.id;
      }
      await api.post(`/api/dashboards/${did}/items`, {
        type: pin.type, title: pin.title, payload: pin.payload, source_run_id: pin.runId ?? null,
      });
      message.success('已固定到仪表板');
      setPin(null);
    } catch { /* client.ts 已提示 */ }
  };

  const skillOk = async () => {
    if (!skillOf) return;
    if (!skillForm.name.trim()) { message.warning('请填写 Skill 名称'); return; }
    try {
      await api.post('/api/skills/from-run', {
        run_id: skillOf, name: skillForm.name.trim(),
        description: skillForm.description, tags: skillForm.tags,
      });
      message.success('已沉淀为 Skill，后续相似问题将自动复用');
      setSkillOf(null);
    } catch { /* client.ts 已提示 */ }
  };

  const onNewSession = async () => {
    const s = await createSession(null);
    navigate(`/chat/${s.id}`);
  };

  const recommended = currentSession?.agent?.recommended_questions?.length
    ? currentSession.agent.recommended_questions
    : GENERIC_QUESTIONS;

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 52px)', overflow: 'hidden' }}>
      {/* 会话侧栏 */}
      <div style={{ width: 260, flexShrink: 0, borderRight: '1px solid #eef2f7', background: '#fff', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 14 }}>
          <Button type="primary" block icon={<PlusOutlined />} onClick={onNewSession}>新会话</Button>
        </div>
        <div className="thin-scroll" style={{ flex: 1, overflow: 'auto', padding: '0 8px 12px' }}>
          {sessions.map((s: SessionInfo) => (
            <div key={s.id}
              className={`session-item ${currentSession?.id === s.id ? 'active' : ''}`}
              onClick={() => navigate(`/chat/${s.id}`)}>
              <div style={{
                fontSize: 13, fontWeight: currentSession?.id === s.id ? 600 : 400,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{s.title || '新会话'}</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 3 }}>
                <span style={{ fontSize: 11.5, color: '#94a3b8' }}>
                  {s.agent ? s.agent.name : `${s.message_count ?? 0} 条`}
                </span>
                <span>
                  <Button type="text" size="small" icon={<EditOutlined />}
                    onClick={(e) => { e.stopPropagation(); setRenameTarget(s); setRenameText(s.title); }}
                    style={{ color: '#cbd5e1' }} />
                  <Popconfirm title="删除该会话？" onConfirm={async (e) => {
                    e?.stopPropagation();
                    await deleteSession(s.id);
                    if (currentSession?.id === s.id) navigate('/chat');
                  }} onCancel={(e) => e?.stopPropagation()}>
                    <Button type="text" size="small" icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                      style={{ color: '#cbd5e1' }} />
                  </Popconfirm>
                </span>
              </div>
            </div>
          ))}
          {!sessions.length && (
            <div style={{ padding: 24, color: '#94a3b8', fontSize: 12.5, textAlign: 'center' }}>暂无会话</div>
          )}
        </div>
      </div>

      {/* 主区 */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', background: '#F6F5F4' }}>
        {/* 顶栏：会话信息 + 数据源选择 */}
        <div style={{ background: '#fff', borderBottom: '1px solid #eef2f7', padding: '10px 20px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 600, fontSize: 14.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320 }}>
            {currentSession?.title || '对话分析'}
          </div>
          {currentSession?.agent && <Tag color="blue">{currentSession.agent.name}</Tag>}
          <div style={{ flex: 1 }} />
          <DatabaseOutlined style={{ color: '#64748b' }} />
          <Select
            mode="multiple" placeholder="选择数据源（可多选）" style={{ minWidth: 280, maxWidth: 480 }}
            value={selectedDsIds} onChange={setSelectedDsIds}
            options={dataSources.map((d) => ({ value: d.id, label: `${d.name}（${d.row_count ?? '?'} 行）` }))}
            optionFilterProp="label" maxTagCount="responsive" size="middle" />
        </div>

        {/* 消息流 */}
        <div ref={scrollRef} className="thin-scroll" style={{ flex: 1, overflow: 'auto', padding: '12px 24px' }}>
          <div style={{ maxWidth: 980, margin: '0 auto' }}>
            {!currentSession && (
              <div style={{ paddingTop: '6vh' }}>
                <div style={{ textAlign: 'center', marginBottom: 24 }}>
                  <div style={{
                    width: 56, height: 56, borderRadius: 16, margin: '0 auto 14px',
                    background: 'linear-gradient(135deg,#7B5CF5,#A18BE8)',
                    color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26,
                  }}>绎</div>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>绎数 · 对话式数据分析</div>
                  <div style={{ fontSize: 13.5, color: '#64748b', marginTop: 6 }}>
                    用自然语言提问，Agent 生成代码在沙箱执行，流式返回图表、表格与结论
                  </div>
                </div>
                <OperationFlow
                  current={dataSources.length ? 2 : 1}
                  steps={[
                    {
                      title: '连接数据源',
                      desc: '上传 CSV / Excel / Parquet，或连接 MySQL / PostgreSQL 并物化为缓存',
                      action: <Button size="small" icon={<DatabaseOutlined />} onClick={() => navigate('/datasources')}>去连接</Button>,
                    },
                    {
                      title: '创建会话',
                      desc: '新会话可绑定场景 Agent（零售 / 制造），获得行业化推荐问题',
                      action: <Button size="small" type="primary" icon={<PlusOutlined />} onClick={onNewSession}>新会话</Button>,
                    },
                    {
                      title: '对话提问',
                      desc: '输入分析诉求，或点击推荐问题直接开始；支持先确认查询口径再执行',
                    },
                    {
                      title: '沉淀资产',
                      desc: '有价值的结果固定到仪表板，验证过的分析沉淀为 Skill 复用',
                    },
                  ]}
                />
              </div>
            )}
            {currentSession && !messages.length && !streaming && (
              <div style={{ paddingTop: '4vh' }}>
                <OperationFlow
                  current={selectedDsIds.length ? 3 : 2}
                  steps={[
                    {
                      title: '连接数据源',
                      desc: dataSources.length ? '已完成：数据源就绪' : '尚未连接数据，请先到数据源页上传或配置',
                      action: !dataSources.length
                        ? <Button size="small" icon={<DatabaseOutlined />} onClick={() => navigate('/datasources')}>去连接</Button>
                        : undefined,
                    },
                    {
                      title: '选择数据源',
                      desc: '在顶部多选框勾选本次分析要用的数据源（可多选）',
                    },
                    {
                      title: '对话提问',
                      desc: '输入分析诉求，或点击下方推荐问题直接开始',
                    },
                    {
                      title: '沉淀资产',
                      desc: '图表固定到仪表板、分析沉淀为 Skill，越用越聪明',
                    },
                  ]}
                />
                {currentSession.agent?.intro && (
                  <div style={{
                    background: '#fff', border: '1px solid #eef2f7', borderRadius: 12,
                    padding: '18px 22px', margin: '20px 0', fontSize: 13.5, color: '#475569', lineHeight: 1.8,
                  }}>{currentSession.agent.intro}</div>
                )}
                <div style={{ textAlign: 'center', color: '#64748b', fontSize: 13.5, marginBottom: 14 }}>
                  试试这样问：
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, justifyContent: 'center' }}>
                  {recommended.map((q) => (
                    <span key={q} className="quick-pill" onClick={() => doAsk(q)}>{q}</span>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => <MessageItem key={m.id} msg={m} cb={cb} />)}
            {streaming && <StreamingItem st={streaming} cb={cb} />}
          </div>
        </div>

        {/* 输入区 */}
        <div style={{ background: '#fff', borderTop: '1px solid #eef2f7', padding: '12px 24px 16px' }}>
          <div style={{ maxWidth: 980, margin: '0 auto' }}>
            <Input.TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); onSend(); } }}
              placeholder={currentSession ? '输入分析问题，Enter 发送，Shift+Enter 换行' : '请先创建会话'}
              autoSize={{ minRows: 1, maxRows: 6 }}
              disabled={!currentSession}
              style={{ borderRadius: 10, fontSize: 14 }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
              <Tooltip title="开启后先解析查询意图（指标/维度/时间范围等），人工确认后再执行">
                <Space size={6}>
                  <CheckSquareOutlined style={{ color: confirmMode ? '#5645D4' : '#94a3b8' }} />
                  <Switch size="small" checked={confirmMode} onChange={setConfirmMode} />
                  <span style={{ fontSize: 12.5, color: '#64748b' }}>先确认查询</span>
                </Space>
              </Tooltip>
              <div style={{ flex: 1 }} />
              <span style={{ fontSize: 12, color: busy ? '#5645D4' : '#cbd5e1' }}>
                {busy ? '分析中…' : `${messages.length} 条消息`}
              </span>
              <Button type="primary" icon={<SendOutlined />} loading={busy}
                disabled={!input.trim() || !currentSession} onClick={onSend}>发送</Button>
            </div>
          </div>
        </div>
      </div>

      {/* spec 确认弹窗 */}
      <Modal
        open={!!confirmSpec}
        title="确认查询口径"
        width={640}
        onOk={confirmSpecOk}
        onCancel={() => {
          if (confirmSpec) setInput(confirmSpec.question);
          setConfirmSpec(null);
        }}
        okText="确认执行" cancelText="返回修改"
      >
        <div style={{ fontSize: 13, color: '#475569', marginBottom: 8 }}>
          问题：<b>{confirmSpec?.question}</b>
        </div>
        <div style={{ fontSize: 12.5, color: '#94a3b8', marginBottom: 12 }}>
          系统解析出的查询规格（QuerySpec）如下，可直接编辑 JSON 后执行：
        </div>
        <Input.TextArea
          value={specText} onChange={(e) => setSpecText(e.target.value)}
          autoSize={{ minRows: 8, maxRows: 18 }}
          style={{ fontFamily: 'Consolas, monospace', fontSize: 12.5 }}
        />
      </Modal>

      {/* 固定到仪表板弹窗 */}
      <Modal
        open={!!pin} title={`固定到仪表板 · ${{ chart: '图表', table: '表格', text: '文本' }[pin?.type ?? 'text']}`}
        onOk={pinOk} onCancel={() => setPin(null)} okText="固定" cancelText="取消"
      >
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>标题</div>
          <Input value={pin?.title} onChange={(e) => setPin((p) => p ? { ...p, title: e.target.value } : p)} />
        </div>
        <div>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>目标仪表板</div>
          <Select
            style={{ width: '100%' }} value={dashChoice === -1 ? undefined : dashChoice}
            onChange={(v) => setDashChoice(v as number | 'new')}
            options={[
              ...dashboards.map((d) => ({ value: d.id, label: `${d.name}（${d.item_count ?? 0} 项）` })),
              { value: 'new', label: '＋ 新建仪表板…' },
            ]}
            placeholder="选择或新建仪表板" />
          {dashChoice === 'new' && (
            <Input
              style={{ marginTop: 8 }} placeholder="新仪表板名称" value={newDashName}
              onChange={(e) => setNewDashName(e.target.value)} />
          )}
        </div>
      </Modal>

      {/* 重命名会话弹窗 */}
      <Modal
        open={!!renameTarget} title="重命名会话" onOk={doRename}
        onCancel={() => setRenameTarget(null)} okText="保存" cancelText="取消"
      >
        <Input
          value={renameText} maxLength={60} style={{ marginTop: 8 }}
          placeholder="会话名称"
          onChange={(e) => setRenameText(e.target.value)}
          onPressEnter={doRename} />
      </Modal>

      {/* 沉淀 Skill 弹窗 */}
      <Modal
        open={!!skillOf} title="沉淀为 Skill" onOk={skillOk} onCancel={() => setSkillOf(null)}
        okText="保存" cancelText="取消"
      >
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>名称</div>
          <Input value={skillForm.name} maxLength={50}
            onChange={(e) => setSkillForm((f) => ({ ...f, name: e.target.value }))} />
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>描述（什么时候复用它）</div>
          <Input.TextArea rows={2} value={skillForm.description} maxLength={200}
            onChange={(e) => setSkillForm((f) => ({ ...f, description: e.target.value }))} />
        </div>
        <div>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>标签</div>
          <Select mode="tags" style={{ width: '100%' }} placeholder="如：零售、趋势、周报"
            value={skillForm.tags} onChange={(tags) => setSkillForm((f) => ({ ...f, tags }))} />
        </div>
      </Modal>
    </div>
  );
}
