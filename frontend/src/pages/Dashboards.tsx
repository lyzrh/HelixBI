/** 仪表板页：列表 + 详情网格（图表/表格/文本/洞察）+ 条目编辑/排序/刷新 + HTML 导出 */

import { useEffect, useState } from 'react';
import {
  App as AntApp, Button, Card, Col, Dropdown, Empty, Input, Modal, Popconfirm,
  Radio, Row, Select, Spin, Tag,
} from 'antd';
import {
  ArrowDownOutlined, ArrowUpOutlined, BarChartOutlined, DeleteOutlined,
  DownloadOutlined, EditOutlined, FileTextOutlined, MoreOutlined,
  PlusOutlined, ReloadOutlined, TableOutlined,
} from '@ant-design/icons';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { EChartsOption } from 'echarts';
import { api } from '../api/client';
import type {
  DashboardInfo, DashboardItemInfo, DataSourceInfo,
} from '../api/types';
import { ResultTable } from '../components/chat/ResultBlocks';
import { AuthImg } from '../components/chat/AuthImg';
import { EChart } from '../components/charts/EChart';
import { useAppStore } from '../stores/appStore';

const TYPE_META: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  chart: { icon: <BarChartOutlined />, label: '图表', color: '#5645D4' },
  table: { icon: <TableOutlined />, label: '表格', color: '#2A9D99' },
  text: { icon: <FileTextOutlined />, label: '结论', color: '#7B5CF5' },
  insight: { icon: <FileTextOutlined />, label: '洞察', color: '#ea580c' },
};

const SPAN_OPTIONS = [
  { value: 6, label: '1/4 宽' },
  { value: 8, label: '1/3 宽' },
  { value: 12, label: '1/2 宽' },
  { value: 24, label: '整行' },
];

const metricLabel = (m: { field: string; agg: string }) =>
  m.agg === 'sum' ? m.field : `${m.field}(${m.agg})`;

/** 按查询规格 + 最新行数据重建 ECharts option（与自助分析页保持一致） */
function buildOptionFromRows(
  spec: NonNullable<DashboardItemInfo['payload']['query_spec']>,
  rows: Record<string, unknown>[],
  kind: string,
): EChartsOption | null {
  if (!rows.length) return null;
  const dims = spec.dimensions ?? [];
  const metrics = spec.metrics ?? [];
  if (!metrics.length) return null;
  const categories = rows.map((r) => dims.map((d) => String(r[d] ?? '-')).join('/'));
  const valueKeys = metrics.map(metricLabel);

  if (kind === 'pie') {
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { orient: 'vertical', left: 'left', top: 'middle' },
      series: [{
        type: 'pie', radius: ['38%', '68%'], center: ['55%', '50%'],
        data: rows.map((r, i) => ({ name: categories[i], value: Number(r[valueKeys[0]]) || 0 })),
        label: { formatter: '{b}\n{d}%' },
      }],
    };
  }
  const series = valueKeys.map((k) => ({
    name: k, type: kind === 'area' ? 'line' : 'bar',
    smooth: kind === 'area',
    areaStyle: kind === 'area' ? { opacity: 0.25 } : undefined,
    data: rows.map((r) => Number(r[k]) || 0),
    barMaxWidth: 42,
  }));
  return {
    tooltip: { trigger: 'axis' },
    legend: valueKeys.length > 1 ? { top: 0 } : undefined,
    grid: { left: 56, right: 24, top: valueKeys.length > 1 ? 36 : 20, bottom: 48 },
    xAxis: {
      type: 'category', data: categories,
      axisLabel: { rotate: categories.length > 8 || categories.some((c) => c.length > 6) ? 32 : 0 },
    },
    yAxis: { type: 'value' },
    series,
    dataZoom: rows.length > 30 ? [{ type: 'slider', height: 16, bottom: 8 }] : undefined,
  };
}

export function Dashboards() {
  const { message } = AntApp.useApp();

  const [dashboards, setDashboards] = useState<DashboardInfo[]>([]);
  const [current, setCurrent] = useState<DashboardInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const dataSources = useAppStore((s) => s.dataSources);
  const loadDataSources = useAppStore((s) => s.loadDataSources);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);

  // 新建 / 编辑
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<DashboardInfo | null>(null);
  const [form, setForm] = useState({ name: '', description: '' });
  const [saving, setSaving] = useState(false);

  // 条目编辑（标题 + 宽度）
  const [itemEdit, setItemEdit] = useState<DashboardItemInfo | null>(null);
  const [itemForm, setItemForm] = useState({ title: '', span: 12 });

  useEffect(() => { loadDataSources(); }, [loadDataSources]);

  const reloadList = async (selectId?: number) => {
    setLoading(true);
    try {
      const list = await api.get<DashboardInfo[]>('/api/dashboards');
      setDashboards(list);
      const target = selectId ?? current?.id ?? list[0]?.id;
      if (target) {
        const d = await api.get<DashboardInfo>(`/api/dashboards/${target}`);
        setCurrent(d);
      } else {
        setCurrent(null);
      }
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reloadList(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openDashboard = async (id: number) => {
    setLoading(true);
    try {
      setCurrent(await api.get<DashboardInfo>(`/api/dashboards/${id}`));
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', description: '' });
    setEditOpen(true);
  };

  const openEdit = () => {
    if (!current) return;
    setEditing(current);
    setForm({ name: current.name, description: current.description });
    setEditOpen(true);
  };

  const save = async () => {
    if (!form.name.trim()) { message.warning('请填写名称'); return; }
    setSaving(true);
    try {
      if (editing) {
        await api.patch(`/api/dashboards/${editing.id}`, {
          name: form.name.trim(), description: form.description,
        });
        message.success('已保存');
      } else {
        const d = await api.post<DashboardInfo>('/api/dashboards', {
          name: form.name.trim(), description: form.description,
        });
        message.success('已创建');
        await reloadList(d.id);
      }
      setEditOpen(false);
      if (editing) await reloadList(editing.id);
    } catch { /* client.ts 已提示 */ } finally {
      setSaving(false);
    }
  };

  const remove = async (d: DashboardInfo) => {
    try {
      await api.del(`/api/dashboards/${d.id}`);
      message.success('已删除');
      setCurrent(null);
      reloadList();
    } catch { /* client.ts 已提示 */ }
  };

  const removeItem = async (item: DashboardItemInfo) => {
    if (!current) return;
    try {
      await api.del(`/api/dashboards/items/${item.id}`);
      setCurrent({ ...current, items: (current.items || []).filter((x) => x.id !== item.id) });
      reloadList(current.id);
    } catch { /* client.ts 已提示 */ }
  };

  /** 上移 / 下移：本地重排 + 后端持久化 */
  const moveItem = async (item: DashboardItemInfo, dir: -1 | 1) => {
    if (!current) return;
    const items = [...(current.items || [])];
    const idx = items.findIndex((x) => x.id === item.id);
    const target = idx + dir;
    if (idx < 0 || target < 0 || target >= items.length) return;
    [items[idx], items[target]] = [items[target], items[idx]];
    setCurrent({ ...current, items });
    try {
      await api.post(`/api/dashboards/${current.id}/items/reorder`, { ids: items.map((x) => x.id) });
    } catch { /* client.ts 已提示 */ reloadList(current.id); }
  };

  /** 实时组件刷新：按保存的查询规格重新取数并更新图表 */
  const refreshItem = async (item: DashboardItemInfo) => {
    const { data_source_id, query_spec } = item.payload;
    if (!data_source_id || !query_spec) return;
    setRefreshingId(item.id);
    try {
      const r = await api.post<{ rows: Record<string, unknown>[] }>('/api/explore/query', {
        data_source_id, ...query_spec,
      });
      const rows = r.rows ?? [];
      let payload: DashboardItemInfo['payload'];
      if (item.type === 'table') {
        payload = { ...item.payload, rows: rows.slice(0, 100) };
      } else {
        const option = buildOptionFromRows(query_spec, rows, item.payload.chart_kind ?? 'bar');
        payload = { ...item.payload, chart_option: option ?? item.payload.chart_option, rows: rows.slice(0, 200) };
      }
      const updated = await api.patch<DashboardItemInfo>(
        `/api/dashboards/items/${item.id}`, { payload });
      setCurrent((cur) => cur ? ({
        ...cur,
        items: (cur.items || []).map((x) => (x.id === item.id ? { ...x, payload: updated.payload } : x)),
      }) : cur);
      message.success(`已刷新「${item.title || TYPE_META[item.type]?.label}」，共 ${rows.length} 组数据`);
    } catch { /* client.ts 已提示 */ } finally {
      setRefreshingId(null);
    }
  };

  const openItemEdit = (item: DashboardItemInfo) => {
    setItemEdit(item);
    setItemForm({ title: item.title, span: item.span ?? 12 });
  };

  const saveItemEdit = async () => {
    if (!itemEdit) return;
    try {
      const updated = await api.patch<DashboardItemInfo>(`/api/dashboards/items/${itemEdit.id}`, {
        title: itemForm.title.trim(), span: itemForm.span,
      });
      setCurrent((cur) => cur ? ({
        ...cur,
        items: (cur.items || []).map((x) => (x.id === itemEdit.id ? updated : x)),
      }) : cur);
      message.success('已保存');
      setItemEdit(null);
    } catch { /* client.ts 已提示 */ }
  };

  const exportHtml = (d: DashboardInfo) => {
    window.open(`/api/dashboards/${d.id}/export`, '_blank');
  };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 52px)', overflow: 'hidden' }}>
      {/* 仪表板列表 */}
      <div style={{ width: 240, flexShrink: 0, borderRight: '1px solid #eef2f7', background: '#fff', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '16px 14px 10px', display: 'flex', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>仪表板</div>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>对话中固定的分析资产</div>
          </div>
          <div style={{ flex: 1 }} />
          <Button type="primary" size="small" icon={<PlusOutlined />} onClick={openCreate} />
        </div>
        <div className="thin-scroll" style={{ flex: 1, overflow: 'auto', padding: '0 8px 12px' }}>
          {dashboards.map((d) => (
            <div key={d.id}
              className={`session-item ${current?.id === d.id ? 'active' : ''}`}
              onClick={() => openDashboard(d.id)}>
              <div style={{
                fontSize: 13, fontWeight: current?.id === d.id ? 600 : 400,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{d.name}</div>
              <div style={{ fontSize: 11.5, color: '#94a3b8', marginTop: 3 }}>
                {d.item_count ?? 0} 个条目
              </div>
            </div>
          ))}
          {!dashboards.length && (
            <div style={{ padding: 24, color: '#94a3b8', fontSize: 12.5, textAlign: 'center' }}>
              暂无仪表板
            </div>
          )}
        </div>
      </div>

      {/* 详情：FineBI 风格画布（浅灰底 + 标题栏卡片 + 内容卡片网格） */}
      <div className="thin-scroll" style={{ flex: 1, minWidth: 0, overflow: 'auto', background: '#F6F5F4', padding: 16 }}>
        <Spin spinning={loading}>
          {current ? (
            <>
              {/* 标题栏卡片 */}
              <div style={{
                background: '#fff', borderRadius: 10, padding: '14px 18px', marginBottom: 14,
                display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                border: '1px solid #eef2f7',
              }}>
                <div style={{
                  width: 6, height: 34, borderRadius: 3,
                  background: 'linear-gradient(180deg,#5645D4,#7B5CF5)', flexShrink: 0,
                }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{current.name}</div>
                  {current.description && (
                    <div style={{ fontSize: 12.5, color: '#94a3b8', marginTop: 2 }}>
                      {current.description}
                    </div>
                  )}
                </div>
                <Tag style={{ marginLeft: 6 }}>{current.items?.length ?? 0} 个组件</Tag>
                <div style={{ flex: 1 }} />
                <Button size="small" icon={<EditOutlined />} onClick={openEdit}>编辑</Button>
                <Button size="small" icon={<DownloadOutlined />} onClick={() => exportHtml(current)}>导出 HTML</Button>
                <Popconfirm title={`删除仪表板「${current.name}」及其所有条目？`} onConfirm={() => remove(current)}>
                  <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              </div>

              {/* 组件网格 */}
              <Row gutter={[14, 14]}>
                {(current.items || []).map((item, idx) => {
                  const meta = TYPE_META[item.type] ?? TYPE_META.text;
                  const refreshable = !!item.payload.query_spec && !!item.payload.data_source_id;
                  const items = current.items || [];
                  const menu = {
                    items: [
                      { key: 'edit', icon: <EditOutlined />, label: '编辑标题 / 宽度' },
                      ...(refreshable ? [{ key: 'refresh', icon: <ReloadOutlined />, label: '刷新数据' }] : []),
                      { type: 'divider' as const },
                      { key: 'up', icon: <ArrowUpOutlined />, label: '上移', disabled: idx === 0 },
                      { key: 'down', icon: <ArrowDownOutlined />, label: '下移', disabled: idx === items.length - 1 },
                      { type: 'divider' as const },
                      { key: 'del', icon: <DeleteOutlined />, label: '移除', danger: true },
                    ],
                    onClick: ({ key }: { key: string }) => {
                      if (key === 'edit') openItemEdit(item);
                      else if (key === 'refresh') refreshItem(item);
                      else if (key === 'up') moveItem(item, -1);
                      else if (key === 'down') moveItem(item, 1);
                      else if (key === 'del') Modal.confirm({
                        title: '移除该条目？', content: item.title,
                        onOk: () => removeItem(item),
                      });
                    },
                  };
                  return (
                    <Col key={item.id} xs={24} md={item.span ?? 12}>
                      <Card
                        size="small"
                        style={{
                          borderRadius: 10, border: '1px solid #eef2f7',
                          boxShadow: '0 1px 3px rgba(15,23,42,0.04)',
                        }}
                        styles={{ body: { padding: '8px 10px 10px' } }}
                        title={(
                          <span style={{ fontSize: 13.5, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ color: meta.color }}>{meta.icon}</span>
                            {item.title || meta.label}
                            {refreshable && (
                              <Tag color="cyan" style={{ fontSize: 10, lineHeight: '16px', marginRight: 0 }}>实时</Tag>
                            )}
                          </span>
                        )}
                        extra={(
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                            {refreshable && (
                              <Button type="text" size="small" icon={<ReloadOutlined />}
                                loading={refreshingId === item.id}
                                title="按保存的查询条件重新取数"
                                onClick={() => refreshItem(item)} style={{ color: '#64748b' }} />
                            )}
                            <Dropdown menu={menu} trigger={['click']}>
                              <Button type="text" size="small" icon={<MoreOutlined />} style={{ color: '#94a3b8' }} />
                            </Dropdown>
                          </span>
                        )}
                      >
                        {item.type === 'chart' && item.payload.chart_option && (
                          <EChart option={item.payload.chart_option as EChartsOption} height={320} />
                        )}
                        {item.type === 'chart' && !item.payload.chart_option && item.payload.chart_url && (
                          <AuthImg src={item.payload.chart_url} alt={item.title}
                            style={{ width: '100%', maxHeight: 380, objectFit: 'contain', borderRadius: 6 }} />
                        )}
                        {item.type === 'table' && item.payload.rows && (
                          <ResultTable rows={item.payload.rows} />
                        )}
                        {(item.type === 'text' || item.type === 'insight') && item.payload.text && (
                          <div className="answer-md" style={{
                            background: item.type === 'insight' ? '#fff7ed' : '#faf5ff',
                            border: `1px solid ${item.type === 'insight' ? '#fed7aa' : '#e9d5ff'}`,
                            borderRadius: 8, padding: '10px 14px', fontSize: 13,
                          }}>
                            <Markdown remarkPlugins={[remarkGfm]}>{item.payload.text}</Markdown>
                          </div>
                        )}
                      </Card>
                    </Col>
                  );
                })}
              </Row>

              {!current.items?.length && (
                <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #eef2f7' }}>
                  <Empty
                    style={{ padding: '60px 0' }}
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无条目：在对话分析中点图钉固定图表 / 表格 / 结论，或在洞察页固定诊断"
                  />
                </div>
              )}
            </>
          ) : (
            <Empty
              style={{ marginTop: '20vh' }}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={dashboards.length ? '选择左侧仪表板查看' : '点击左上角 + 新建仪表板'}
            />
          )}
        </Spin>
      </div>

      {/* 条目编辑弹窗（标题 + 宽度） */}
      <Modal
        open={!!itemEdit} title="编辑组件" onOk={saveItemEdit}
        onCancel={() => setItemEdit(null)} okText="保存" cancelText="取消"
      >
        <div style={{ marginBottom: 12, marginTop: 8 }}>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 4 }}>标题</div>
          <Input value={itemForm.title} maxLength={40}
            onChange={(e) => setItemForm((f) => ({ ...f, title: e.target.value }))} />
        </div>
        <div>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 4 }}>卡片宽度</div>
          <Radio.Group
            options={SPAN_OPTIONS} value={itemForm.span} optionType="button"
            onChange={(e) => setItemForm((f) => ({ ...f, span: e.target.value as number }))}
          />
        </div>
      </Modal>

      {/* 新建 / 编辑弹窗 */}
      <Modal
        open={editOpen}
        title={editing ? `编辑仪表板 · ${editing.name}` : '新建仪表板'}
        onOk={save} onCancel={() => setEditOpen(false)}
        okText="保存" cancelText="取消" confirmLoading={saving}
      >
        <div style={{ marginBottom: 12, marginTop: 8 }}>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 4 }}>名称</div>
          <Input value={form.name} maxLength={40} placeholder="如：月度经营复盘"
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </div>
        <div>
          <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 4 }}>描述</div>
          <Input.TextArea rows={2} maxLength={200} value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
        </div>
      </Modal>
    </div>
  );
}
