/** Skill 库页：列表筛选 + 运行（SSE 弹窗）+ 启停 / 编辑 / 删除 */

import { useEffect, useMemo, useState } from 'react';
import {
  App as AntApp, Button, Empty, Input, Modal, Popconfirm, Progress, Select,
  Space, Switch, Table, Tag, Tooltip,
} from 'antd';
import {
  CaretRightOutlined, CheckCircleOutlined, EditOutlined, ExperimentOutlined, SearchOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../api/client';
import { sseStream } from '../api/sse';
import type { SkillInfo, StreamingState } from '../api/types';
import { AnswerCard, ChartGallery, TableTabs, TerminalCollapse } from '../components/chat/ResultBlocks';
import { StepProgress } from '../components/chat/StepProgress';
import { applyEvent } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';

const emptyStreaming = (): StreamingState => ({
  running: true, steps: [], attempts: 0, charts: [], tables: {}, followups: [],
});

export function Skills() {
  const { message } = AntApp.useApp();

  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [packFilter, setPackFilter] = useState<string | null>(null);
  const packs = useAppStore((s) => s.packs);
  const dataSources = useAppStore((s) => s.dataSources);
  const loadPacks = useAppStore((s) => s.loadPacks);
  const loadDataSources = useAppStore((s) => s.loadDataSources);

  // 运行弹窗
  const [runSkill, setRunSkill] = useState<SkillInfo | null>(null);
  const [runDsIds, setRunDsIds] = useState<number[]>([]);
  const [stream, setStream] = useState<StreamingState | null>(null);
  // 编辑弹窗
  const [editSkill, setEditSkill] = useState<SkillInfo | null>(null);
  const [editForm, setEditForm] = useState({ name: '', description: '', tags: [] as string[] });
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      setSkills(await api.get<SkillInfo[]>('/api/skills'));
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
    loadPacks();
    loadDataSources();
  }, [loadPacks, loadDataSources]);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return skills.filter((s) => {
      if (packFilter && s.pack_id !== packFilter) return false;
      if (!kw) return true;
      return (s.name + s.description + s.question + s.tags.join(' ')).toLowerCase().includes(kw);
    });
  }, [skills, keyword, packFilter]);

  const toggleEnabled = async (s: SkillInfo, enabled: boolean) => {
    try {
      await api.patch(`/api/skills/${s.id}`, { enabled });
      setSkills((list) => list.map((x) => (x.id === s.id ? { ...x, enabled } : x)));
    } catch { /* client.ts 已提示 */ }
  };

  const openRun = (s: SkillInfo) => {
    setRunSkill(s);
    setRunDsIds([]);
    setStream(null);
  };

  const startRun = async () => {
    if (!runSkill) return;
    setStream(emptyStreaming());
    try {
      await sseStream(`/api/skills/${runSkill.id}/run`, {
        session_id: null, data_source_ids: runDsIds,
      }, (evt) => {
        setStream((st) => (st ? applyEvent(st, evt) : st));
      });
    } catch {
      setStream((st) => (st ? { ...st, running: false, error: '网络错误' } : st));
    }
    reload(); // 刷新使用统计
  };

  const openEdit = (s: SkillInfo) => {
    setEditSkill(s);
    setEditForm({ name: s.name, description: s.description, tags: s.tags });
  };

  const saveEdit = async () => {
    if (!editSkill) return;
    if (!editForm.name.trim()) { message.warning('请填写名称'); return; }
    setSaving(true);
    try {
      await api.patch(`/api/skills/${editSkill.id}`, {
        name: editForm.name.trim(), description: editForm.description, tags: editForm.tags,
      });
      message.success('已保存');
      setEditSkill(null);
      reload();
    } catch { /* client.ts 已提示 */ } finally {
      setSaving(false);
    }
  };

  const remove = async (s: SkillInfo) => {
    try {
      await api.del(`/api/skills/${s.id}`);
      message.success('已删除');
      reload();
    } catch { /* client.ts 已提示 */ }
  };

  const columns: ColumnsType<SkillInfo> = [
    {
      title: 'Skill', dataIndex: 'name', width: '30%',
      render: (_, s) => (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontWeight: 600, fontSize: 13.5 }}>{s.name}</span>
            {s.builtin && <Tag style={{ fontSize: 11 }}>内置</Tag>}
          </div>
          <div style={{ fontSize: 12.5, color: '#64748b', marginTop: 2 }}>{s.description}</div>
          <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>原问题：{s.question}</div>
        </div>
      ),
    },
    {
      title: '标签', dataIndex: 'tags', width: '16%',
      render: (tags: string[]) => (
        <>
          {tags.map((t) => <Tag key={t} color="blue" style={{ fontSize: 11.5 }}>{t}</Tag>)}
        </>
      ),
    },
    {
      title: '语义包', dataIndex: 'pack_id', width: '12%',
      render: (p: string | null) => p
        ? <Tag color="purple" style={{ fontSize: 11.5 }}>{packs.find((x) => x.id === p)?.name ?? p}</Tag>
        : <span style={{ color: '#cbd5e1' }}>-</span>,
    },
    {
      title: '使用 / 成功', width: '12%',
      render: (_, s) => (
        <span style={{ fontSize: 13 }}>
          {s.use_count} 次
          <span style={{ color: s.success_count === s.use_count && s.use_count > 0 ? '#16a34a' : '#94a3b8', marginLeft: 4 }}>
            （{s.success_count} 成功）
          </span>
        </span>
      ),
    },
    {
      title: '启用', dataIndex: 'enabled', width: '8%',
      render: (_, s) => <Switch size="small" checked={s.enabled} onChange={(v) => toggleEnabled(s, v)} />,
    },
    {
      title: '操作', width: '20%',
      render: (_, s) => (
        <Space size={4}>
          <Button type="primary" size="small" icon={<CaretRightOutlined />}
            disabled={!s.enabled} onClick={() => openRun(s)}>运行</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(s)}>编辑</Button>
          {!s.builtin && (
            <Popconfirm title={`删除 Skill「${s.name}」？`} onConfirm={() => remove(s)}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const runFinished = stream && !stream.running;

  // 概览统计
  const enabledCount = skills.filter((s) => s.enabled).length;
  const totalRuns = skills.reduce((sum, s) => sum + s.use_count, 0);
  const okRuns = skills.reduce((sum, s) => sum + s.success_count, 0);
  const okRate = totalRuns ? Math.round((okRuns / totalRuns) * 100) : 0;

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      {/* 概览统计卡（FineDataLink 概览模式：总数 + 环形进度） */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ ...overviewCardStyle, flex: '1 1 200px' }}>
          <ExperimentOutlined style={{ fontSize: 26, color: '#d97706' }} />
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>Skill 总数</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{skills.length}</div>
            <div style={{ fontSize: 11.5, color: '#94a3b8' }}>内置 {skills.filter((s) => s.builtin).length} · 自定义 {skills.filter((s) => !s.builtin).length}</div>
          </div>
        </div>
        <div style={{ ...overviewCardStyle, flex: '1 1 150px' }}>
          <Progress type="circle" size={56} percent={skills.length ? Math.round((enabledCount / skills.length) * 100) : 0}
            strokeColor="#16a34a" format={() => `${enabledCount}`} />
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>已启用</div>
            <div style={{ fontSize: 13, color: '#16a34a', fontWeight: 600 }}>
              {skills.length ? `${Math.round((enabledCount / skills.length) * 100)}%` : '-'}
            </div>
          </div>
        </div>
        <div style={{ ...overviewCardStyle, flex: '1 1 150px' }}>
          <Progress type="circle" size={56} percent={okRate}
            strokeColor={okRate >= 80 ? '#2A9D99' : '#ea580c'} format={() => `${okRate}%`} />
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>重放成功率</div>
            <div style={{ fontSize: 11.5, color: '#94a3b8' }}>{okRuns} / {totalRuns} 次</div>
          </div>
        </div>
        <div style={{ ...overviewCardStyle, flex: '1 1 200px' }}>
          <CaretRightOutlined style={{ fontSize: 26, color: '#5645D4' }} />
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>累计重放次数</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{totalRuns}</div>
            <div style={{ fontSize: 11.5, color: '#94a3b8' }}>成功 {okRuns} · 失败 {totalRuns - okRuns}</div>
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>Skill 库</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            验证过的分析路径自动沉淀：相似问题直接重放代码（秒回），列不匹配时作为 few-shot 参考重新生成
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <Input allowClear prefix={<SearchOutlined />} placeholder="搜索名称 / 描述 / 问题"
          style={{ width: 240 }} value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <Select style={{ width: 160 }} placeholder="语义包" allowClear
          value={packFilter} onChange={(v) => setPackFilter(v ?? null)}
          options={packs.map((p) => ({ value: p.id, label: p.name }))} />
      </div>

      <Table
        rowKey="id" loading={loading} columns={columns} dataSource={filtered}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无 Skill：在对话页对成功结果点「沉淀为 Skill」" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        style={{ background: '#fff', borderRadius: 12 }}
      />

      {/* 运行弹窗 */}
      <Modal
        open={!!runSkill}
        title={`运行 Skill · ${runSkill?.name ?? ''}`}
        width={860}
        footer={null}
        onCancel={() => { if (!stream?.running) setRunSkill(null); else message.warning('运行中，请稍候'); }}
        destroyOnClose
      >
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 4 }}>
            数据源（留空自动选择该语义包下的数据源）
          </div>
          <Select mode="multiple" style={{ width: '100%' }} placeholder="留空 = 自动选择"
            value={runDsIds} maxTagCount="responsive" disabled={!!stream?.running}
            onChange={setRunDsIds}
            options={dataSources.map((d) => ({ value: d.id, label: `${d.name}（${d.row_count ?? '?'} 行）` }))} />
        </div>
        {!stream && (
          <Button type="primary" icon={<CaretRightOutlined />} onClick={startRun}>开始运行</Button>
        )}
        {stream && (
          <div style={{ borderTop: '1px solid #eef2f7', paddingTop: 12 }}>
            <StepProgress steps={stream.steps} />
            {stream.error ? (
              <div className="stream-error">{stream.error}</div>
            ) : (
              <>
                {stream.running && !stream.answer && !stream.charts.length && (
                  <div className="thinking"><span className="dot" /><span className="dot" /><span className="dot" /></div>
                )}
                {stream.answer && <AnswerCard text={stream.answer} />}
                <ChartGallery charts={stream.charts} />
                <TableTabs tables={stream.tables} />
                <TerminalCollapse stdout={stream.stdout} stderr={stream.stderr} />
                {runFinished && (
                  <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ color: '#16a34a', fontSize: 13 }}>
                      <CheckCircleOutlined /> 运行完成
                    </span>
                    <span style={{ fontSize: 12, color: '#94a3b8' }}>结果已存入会话「Skill：{runSkill?.name}」</span>
                    <div style={{ flex: 1 }} />
                    <Button size="small" onClick={() => setRunSkill(null)}>关闭</Button>
                  </div>
                )}
              </>
            )}
            {runFinished && stream.error && (
              <div style={{ marginTop: 12, textAlign: 'right' }}>
                <Button size="small" onClick={() => setRunSkill(null)}>关闭</Button>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        open={!!editSkill}
        title={`编辑 Skill · ${editSkill?.name ?? ''}`}
        onOk={saveEdit} onCancel={() => setEditSkill(null)}
        okText="保存" cancelText="取消" confirmLoading={saving}
      >
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 8 }}>
          <div>
            <div style={labelStyle}>名称</div>
            <Input value={editForm.name} maxLength={50}
              onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))} />
          </div>
          <div>
            <div style={labelStyle}>描述（什么时候复用它）</div>
            <Input.TextArea rows={2} maxLength={200} value={editForm.description}
              onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div>
            <div style={labelStyle}>标签</div>
            <Select mode="tags" style={{ width: '100%' }} placeholder="如：零售、趋势、周报"
              value={editForm.tags} onChange={(tags) => setEditForm((f) => ({ ...f, tags }))} />
          </div>
          <Tooltip title="Skill 代码在对话页的分析结果中查看；运行列不匹配时系统会参考它重新生成">
            <div style={{ fontSize: 12, color: '#94a3b8' }}>
              代码与关联列由沉淀时的运行记录决定，不支持手动修改
            </div>
          </Tooltip>
        </Space>
      </Modal>
    </div>
  );
}

const labelStyle: React.CSSProperties = { fontSize: 12.5, color: '#64748b', marginBottom: 4 };
const overviewCardStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
  background: '#fff', borderRadius: 12, border: '1px solid #eef2f7',
  boxShadow: '0 1px 3px rgba(15,23,42,0.04)',
};
