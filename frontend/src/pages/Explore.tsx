/** 自助分析（FineBI 风格拖拽式图表）：字段面板 + 横纵轴 + ECharts 实时渲染，零 LLM */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntApp, Button, Empty, Input, Modal, Select, Space, Spin, Tag, Tooltip,
} from 'antd';
import {
  AreaChartOutlined, BarChartOutlined, DeleteOutlined, DownloadOutlined,
  FieldNumberOutlined, FieldStringOutlined, FilterOutlined, LineChartOutlined,
  PieChartOutlined, ReloadOutlined, SaveOutlined, DotChartOutlined, TableOutlined,
} from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import { api } from '../api/client';
import type { DashboardInfo, DataSourceInfo, ExploreField } from '../api/types';
import { EChart } from '../components/charts/EChart';
import { ResultTable } from '../components/chat/ResultBlocks';
import { useAppStore } from '../stores/appStore';

type ChartKind = 'bar' | 'line' | 'area' | 'scatter' | 'pie' | 'table';
type Agg = 'sum' | 'avg' | 'count' | 'count_distinct' | 'max' | 'min';
type FilterOp = 'eq' | 'ne' | 'gt' | 'lt' | 'ge' | 'le' | 'contains';

const AGG_LABEL: Record<Agg, string> = {
  sum: '求和', avg: '平均', count: '计数', count_distinct: '去重计数', max: '最大', min: '最小',
};

const FILTER_OP_LABEL: Record<FilterOp, string> = {
  eq: '等于', ne: '不等于', gt: '大于', lt: '小于', ge: '≥', le: '≤', contains: '包含',
};

interface MetricSlot { field: string; agg: Agg }
interface FilterSlot { field: string; op: FilterOp; value: string }

const FIELD_ICON: Record<string, React.ReactNode> = {
  text: <FieldStringOutlined style={{ color: '#2563eb' }} />,
  number: <FieldNumberOutlined style={{ color: '#16a34a' }} />,
  date: <FieldStringOutlined style={{ color: '#d97706' }} />,
};
const FIELD_TYPE_LABEL: Record<string, string> = { text: '维度', number: '指标', date: '日期' };

export function Explore() {
  const { message } = AntApp.useApp();

  const dataSources = useAppStore((s) => s.dataSources);
  const loadDataSources = useAppStore((s) => s.loadDataSources);
  useEffect(() => { loadDataSources(); }, [loadDataSources]);

  const [dsId, setDsId] = useState<number | null>(null);
  const [fields, setFields] = useState<ExploreField[]>([]);
  const [rowsTotal, setRowsTotal] = useState<number | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);

  const [search, setSearch] = useState('');
  const [dims, setDims] = useState<string[]>([]);
  const [metrics, setMetrics] = useState<MetricSlot[]>([]);
  const [filters, setFilters] = useState<FilterSlot[]>([]);
  const [dateGrain, setDateGrain] = useState<'day' | 'month'>('day');
  const [chartKind, setChartKind] = useState<ChartKind>('bar');
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [queryLoading, setQueryLoading] = useState(false);
  const [queried, setQueried] = useState(false);

  // 保存到仪表板
  const [saveOpen, setSaveOpen] = useState(false);
  const [dashboards, setDashboards] = useState<DashboardInfo[]>([]);
  const [saveDashId, setSaveDashId] = useState<number | null>(null);
  const [saveTitle, setSaveTitle] = useState('');
  const [saving, setSaving] = useState(false);

  const usableSources = useMemo(
    () => dataSources.filter((d) => d.type === 'file' || d.materialized_path),
    [dataSources],
  );

  // 默认选中第一个可用数据源
  useEffect(() => {
    if (dsId == null && usableSources.length) setDsId(usableSources[0].id);
  }, [usableSources, dsId]);

  const loadSchema = useCallback(async (id: number) => {
    setSchemaLoading(true);
    setDims([]); setMetrics([]); setFilters([]); setRows([]); setQueried(false);
    try {
      const r = await api.get<{ fields: ExploreField[]; rows: number }>(
        `/api/explore/schema?data_source_id=${id}`);
      setFields(r.fields);
      setRowsTotal(r.rows);
    } catch { /* client 已提示 */ } finally {
      setSchemaLoading(false);
    }
  }, []);

  useEffect(() => {
    if (dsId != null) loadSchema(dsId);
  }, [dsId, loadSchema]);

  const fieldByName = useMemo(
    () => Object.fromEntries(fields.map((f) => [f.name, f])), [fields]);

  // 字段加入轴：text/date → 维度，number → 指标
  const addField = (f: ExploreField) => {
    if (f.dtype === 'number') {
      setMetrics((ms) => (ms.some((m) => m.field === f.name) ? ms : [...ms, { field: f.name, agg: 'sum' }]));
    } else {
      setDims((ds) => (ds.includes(f.name) ? ds : [...ds, f.name]));
    }
  };

  const dimFields = fields.filter((f) => f.dtype !== 'number' && f.name.includes(search));
  const numFields = fields.filter((f) => f.dtype === 'number' && f.name.includes(search));

  const metricLabel = (m: MetricSlot) =>
    m.agg === 'sum' ? m.field : `${m.field}(${m.agg})`;

  // 查询
  const runQuery = useCallback(async () => {
    if (dsId == null || !metrics.length) return;
    setQueryLoading(true);
    try {
      const r = await api.post<{ rows: Record<string, unknown>[]; total: number }>(
        '/api/explore/query', {
          data_source_id: dsId,
          dimensions: dims,
          metrics: metrics.map((m) => ({ field: m.field, agg: m.agg })),
          filters: filters
            .filter((f) => f.field && String(f.value).trim() !== '')
            .map((f) => ({
              field: f.field, op: f.op,
              value: f.op === 'contains' ? f.value
                : fieldByName[f.field]?.dtype === 'number' ? Number(f.value) : f.value,
            })),
          date_grain: dims.length === 1 && fieldByName[dims[0]]?.dtype === 'date' ? dateGrain : null,
          sort_field: metrics.length ? metricLabel(metrics[0]) : null,
          sort_order: 'desc',
          limit: 200,
        });
      setRows(r.rows);
      setQueried(true);
    } catch { /* client 已提示 */ } finally {
      setQueryLoading(false);
    }
  }, [dsId, dims, metrics, filters, dateGrain, fieldByName]);

  // 轴变化自动查询（FineBI 风格）；筛选值需按回车/失焦后手动应用
  useEffect(() => {
    if (metrics.length) runQuery();
  }, [metrics, dims, dateGrain]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyFilters = () => {
    if (metrics.length) runQuery();
  };

  /** 当前查询规格（保存到仪表板后，组件可一键刷新重查） */
  const buildQuerySpec = () => ({
    dimensions: dims,
    metrics: metrics.map((m) => ({ field: m.field, agg: m.agg })),
    filters: filters
      .filter((f) => f.field && String(f.value).trim() !== '')
      .map((f) => ({
        field: f.field, op: f.op,
        value: f.op === 'contains' ? f.value
          : fieldByName[f.field]?.dtype === 'number' ? Number(f.value) : f.value,
      })),
    date_grain: dims.length === 1 && fieldByName[dims[0]]?.dtype === 'date' ? dateGrain : null,
    sort_field: metrics.length ? metricLabel(metrics[0]) : null,
    sort_order: 'desc',
    limit: 200,
  });

  // ---- ECharts option 构建 ----

  const categories = useMemo(
    () => rows.map((r) => dims.map((d) => String(r[d] ?? '-')).join('/')),
    [rows, dims]);

  const chartOption = useMemo<EChartsOption | null>(() => {
    if (!rows.length || !metrics.length) return null;
    const valueKeys = metrics.map(metricLabel);

    if (chartKind === 'pie') {
      const key = valueKeys[0];
      return {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { orient: 'vertical', left: 'left', top: 'middle' },
        series: [{
          type: 'pie', radius: ['38%', '68%'], center: ['55%', '50%'],
          data: rows.map((r, i) => ({ name: categories[i], value: Number(r[key]) || 0 })),
          label: { formatter: '{b}\n{d}%' },
        }],
      };
    }

    if (chartKind === 'scatter') {
      // 双指标散点：X=指标1，Y=指标2；仅一个指标时 X 用序号
      const xKey = valueKeys[0];
      const yKey = valueKeys[1] ?? valueKeys[0];
      const single = valueKeys.length < 2;
      return {
        tooltip: {
          trigger: 'item',
          formatter: (p: unknown) => {
            const { dataIndex } = p as { dataIndex: number };
            const label = categories[dataIndex] ?? String(dataIndex + 1);
            return single
              ? `${label}<br/>${yKey}: ${rows[dataIndex]?.[yKey] ?? '-'}`
              : `${label}<br/>${xKey}: ${rows[dataIndex]?.[xKey] ?? '-'}<br/>${yKey}: ${rows[dataIndex]?.[yKey] ?? '-'}`;
          },
        },
        grid: { left: 64, right: 24, top: 24, bottom: 48 },
        xAxis: {
          type: 'value', name: single ? '序号' : xKey, nameLocation: 'middle', nameGap: 28,
          scale: true,
        },
        yAxis: { type: 'value', name: yKey, scale: true },
        series: [{
          type: 'scatter', symbolSize: 12,
          data: rows.map((r, i) => single
            ? [i + 1, Number(r[yKey]) || 0]
            : [Number(r[xKey]) || 0, Number(r[yKey]) || 0]),
        }],
      };
    }

    const series = valueKeys.map((k) => ({
      name: k,
      type: chartKind === 'area' ? 'line' : (chartKind as 'bar' | 'line'),
      smooth: chartKind === 'line' || chartKind === 'area',
      areaStyle: chartKind === 'area' ? { opacity: 0.25 } : undefined,
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
  }, [rows, metrics, chartKind, categories]);

  // ---- 保存到仪表板 ----

  const openSave = async () => {
    if (!chartOption && chartKind !== 'table') { message.warning('当前没有可保存的图表'); return; }
    try {
      setDashboards(await api.get<DashboardInfo[]>('/api/dashboards'));
    } catch { /* client 已提示 */ }
    setSaveTitle(dims.length ? `${dims.join('×')}${metrics.length ? ' · ' + metrics.map(metricLabel).join('、') : ''}`.slice(0, 40) : '自助分析图表');
    setSaveDashId(null);
    setSaveOpen(true);
  };

  const doSave = async () => {
    if (dsId == null) return;
    let did = saveDashId;
    setSaving(true);
    try {
      if (!did) {
        const created = await api.post<DashboardInfo>('/api/dashboards', { name: saveTitle || '自助分析', description: '' });
        did = created.id;
      }
      await api.post(`/api/dashboards/${did}/items`, {
        type: chartKind === 'table' ? 'table' : 'chart',
        title: saveTitle,
        payload: chartKind === 'table'
          ? {
              table_name: saveTitle, rows: rows.slice(0, 100),
              data_source_id: dsId, query_spec: buildQuerySpec(),
            }
          : {
              chart_option: chartOption, chart_kind: chartKind,
              rows: rows.slice(0, 200),
              data_source_id: dsId, query_spec: buildQuerySpec(),
            },
        source_run_id: null,
      });
      message.success(`已保存到仪表板「${dashboards.find((d) => d.id === did)?.name ?? saveTitle}」`);
      setSaveOpen(false);
    } catch { /* client 已提示 */ } finally {
      setSaving(false);
    }
  };

  /** 客户端导出当前聚合结果为 CSV（BOM + 转义，Excel 直开） */
  const exportCsv = () => {
    if (!rows.length) return;
    const cols = [...dims, ...metrics.map((m) => m.field)];
    const esc = (v: unknown) => {
      const s = String(v ?? '');
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))];
    const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `自助分析_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    message.success(`已导出 ${rows.length} 行`);
  };

  // ---- 渲染 ----

  const ds = dataSources.find((d) => d.id === dsId) as DataSourceInfo | undefined;
  const dateDimSelected = dims.length === 1 && fieldByName[dims[0]]?.dtype === 'date';

  const chartTools = (
    <Space size={4} style={{ background: '#f1f5f9', padding: 3, borderRadius: 8 }}>
      {([
        ['bar', <BarChartOutlined />], ['line', <LineChartOutlined />],
        ['area', <AreaChartOutlined />], ['scatter', <DotChartOutlined />],
        ['pie', <PieChartOutlined />], ['table', <TableOutlined />],
      ] as [ChartKind, React.ReactNode][]).map(([kind, icon]) => (
        <Tooltip key={kind} title={{
          bar: '柱状图', line: '折线图', area: '面积图',
          scatter: '散点图（需两个指标）', pie: '饼图', table: '数据表',
        }[kind]}>
          <Button size="small" type={chartKind === kind ? 'primary' : 'text'} icon={icon}
            onClick={() => setChartKind(kind)} />
        </Tooltip>
      ))}
    </Space>
  );

  return (
    <div style={{ padding: 20, height: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 顶栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 19, fontWeight: 700 }}>自助分析</div>
          <div style={{ fontSize: 12.5, color: '#64748b' }}>拖拽或点击字段到横纵轴，即时出图（本地计算，不消耗模型额度）</div>
        </div>
        <div style={{ flex: 1 }} />
        <Select style={{ minWidth: 240 }} placeholder="选择数据源" value={dsId ?? undefined}
          onChange={setDsId} showSearch optionFilterProp="label"
          options={usableSources.map((d) => ({ value: d.id, label: `${d.name}（${d.row_count ?? '?'} 行）` }))} />
        <Button icon={<ReloadOutlined />} onClick={() => dsId != null && loadSchema(dsId)}>刷新字段</Button>
        <Button icon={<DownloadOutlined />} disabled={!rows.length} onClick={exportCsv}>导出 CSV</Button>
        <Button type="primary" icon={<SaveOutlined />} disabled={!rows.length} onClick={openSave}>保存到仪表板</Button>
      </div>

      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
        {/* 字段面板 */}
        <div style={{ width: 232, background: '#fff', borderRadius: 12, border: '1px solid #eef2f7', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Input.Search placeholder="搜索字段" value={search} onChange={(e) => setSearch(e.target.value)} allowClear size="small" />
          <Spin spinning={schemaLoading}>
            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 272px)' }}>
              <div style={sectionTitleStyle}>
                维度 <Tag style={{ fontSize: 10 }}>{dimFields.length}</Tag>
              </div>
              {dimFields.map((f) => (
                <div key={f.name} style={fieldItemStyle} draggable
                  onDragStart={(e) => e.dataTransfer.setData('text/plain', JSON.stringify(f))}
                  onClick={() => addField(f)}
                  title={`例: ${f.sample}`}>
                  {FIELD_ICON[f.dtype]}
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
                  <span style={{ fontSize: 10, color: '#cbd5e1' }}>{f.dtype === 'date' ? '日期' : 'T'}</span>
                </div>
              ))}
              {!dimFields.length && <div style={emptyHintStyle}>无匹配字段</div>}

              <div style={{ ...sectionTitleStyle, marginTop: 12 }}>
                指标 <Tag style={{ fontSize: 10 }}>{numFields.length}</Tag>
              </div>
              {numFields.map((f) => (
                <div key={f.name} style={fieldItemStyle} draggable
                  onDragStart={(e) => e.dataTransfer.setData('text/plain', JSON.stringify(f))}
                  onClick={() => addField(f)}
                  title={`例: ${f.sample}`}>
                  {FIELD_ICON.number}
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
                  <span style={{ fontSize: 10, color: '#cbd5e1' }}>#</span>
                </div>
              ))}
              {!numFields.length && <div style={emptyHintStyle}>无匹配字段</div>}
            </div>
          </Spin>
        </div>

        {/* 主区 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          {/* 配轴区 */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #eef2f7', padding: '10px 14px', display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}
            onDragOver={(e) => e.preventDefault()}>
            <div style={{ flex: '1 1 320px', minWidth: 260 }}>
              <div style={axisTitleStyle}>
                横轴（维度）
                {dateDimSelected && (
                  <Select size="small" style={{ marginLeft: 8, width: 76 }} value={dateGrain}
                    onChange={setDateGrain} options={[{ value: 'day', label: '按日' }, { value: 'month', label: '按月' }]} />
                )}
              </div>
              <div style={slotBoxStyle}
                onDrop={(e) => {
                  e.preventDefault();
                  try { addField(JSON.parse(e.dataTransfer.getData('text/plain')) as ExploreField); } catch { /* 忽略 */ }
                }}>
                {dims.length ? dims.map((d) => (
                  <Tag key={d} closable style={slotTagStyle} onClose={() => setDims((x) => x.filter((y) => y !== d))}>
                    {fieldByName[d]?.dtype === 'date' ? '📅 ' : ''}{d}
                  </Tag>
                )) : <span style={emptyHintStyle}>拖入维度字段（文本 / 日期）</span>}
              </div>
            </div>
            <div style={{ flex: '1 1 380px', minWidth: 300 }}>
              <div style={axisTitleStyle}>纵轴（指标）</div>
              <div style={slotBoxStyle}
                onDrop={(e) => {
                  e.preventDefault();
                  try {
                    const f = JSON.parse(e.dataTransfer.getData('text/plain')) as ExploreField;
                    if (f.dtype !== 'number') { message.info('数值字段请拖到纵轴（指标）'); return; }
                    addField(f);
                  } catch { /* 忽略 */ }
                }}>
                {metrics.length ? metrics.map((m, i) => (
                  <span key={m.field} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <Tag closable style={slotTagStyle} onClose={() => setMetrics((x) => x.filter((_, j) => j !== i))}>
                      {m.field}
                    </Tag>
                    <Select size="small" style={{ width: 92 }} value={m.agg}
                      onChange={(v) => setMetrics((x) => x.map((y, j) => (j === i ? { ...y, agg: v as Agg } : y)))}
                      options={(Object.keys(AGG_LABEL) as Agg[]).map((a) => ({ value: a, label: AGG_LABEL[a] }))} />
                  </span>
                )) : <span style={emptyHintStyle}>拖入指标字段（数值），可切换聚合方式</span>}
              </div>
            </div>
          </div>

          {/* 筛选器 */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #eef2f7', padding: '8px 14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={axisTitleStyle}><FilterOutlined /> 筛选器</span>
              {filters.map((f, i) => (
                <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <Select size="small" style={{ width: 130 }} placeholder="字段" value={f.field || undefined}
                    showSearch optionFilterProp="label"
                    onChange={(v) => setFilters((x) => x.map((y, j) => (j === i ? { ...y, field: v } : y)))}
                    options={fields.map((fl) => ({ value: fl.name, label: fl.name }))} />
                  <Select size="small" style={{ width: 84 }} value={f.op}
                    onChange={(v) => {
                      setFilters((x) => x.map((y, j) => (j === i ? { ...y, op: v as FilterOp } : y)));
                      setTimeout(applyFilters, 0);
                    }}
                    options={(Object.keys(FILTER_OP_LABEL) as FilterOp[]).map((o) => ({ value: o, label: FILTER_OP_LABEL[o] }))} />
                  <Input size="small" style={{ width: 120 }} placeholder="筛选值"
                    value={f.value}
                    onChange={(e) => setFilters((x) => x.map((y, j) => (j === i ? { ...y, value: e.target.value } : y)))}
                    onPressEnter={applyFilters}
                    onBlur={applyFilters} />
                  <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#cbd5e1' }}
                    onClick={() => {
                      setFilters((x) => x.filter((_, j) => j !== i));
                      setTimeout(applyFilters, 0);
                    }} />
                </span>
              ))}
              <Button size="small" type="dashed" icon={<FilterOutlined />}
                onClick={() => setFilters((x) => [...x, { field: fields[0]?.name ?? '', op: 'eq', value: '' }])}>
                添加筛选
              </Button>
              {filters.length > 0 && (
                <span style={{ fontSize: 11.5, color: '#94a3b8' }}>值输完后按回车生效</span>
              )}
            </div>
          </div>

          {/* 图表区 */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #eef2f7', padding: 14, flex: 1, minHeight: 320, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              {chartTools}
              <div style={{ flex: 1 }} />
              {queried && rows.length > 0 && (
                <span style={{ fontSize: 12, color: '#94a3b8' }}>
                  {ds?.name} · {rowsTotal != null ? `${rowsTotal} 行原始数据` : ''} · 展示 {rows.length} 组
                </span>
              )}
            </div>
            <Spin spinning={queryLoading} style={{ flex: 1 }}>
              <div style={{ flex: 1, minHeight: 0 }}>
                {!metrics.length ? (
                  <Empty style={{ marginTop: 80 }} description="从左侧点击或拖拽字段开始分析" />
                ) : chartKind === 'table' ? (
                  <div style={{ maxHeight: 'calc(100vh - 432px)', overflow: 'auto' }}>
                    <ResultTable rows={rows} />
                  </div>
                ) : chartOption ? (
                  <EChart option={chartOption} height={Math.max(340, Math.min(560, rows.length * 34 + 160))} />
                ) : (
                  <Empty style={{ marginTop: 80 }} description="暂无数据" />
                )}
              </div>
            </Spin>
          </div>
        </div>
      </div>

      {/* 保存弹窗 */}
      <Modal open={saveOpen} title="保存到仪表板" onOk={doSave} onCancel={() => setSaveOpen(false)}
        okText="保存" cancelText="取消" confirmLoading={saving}>
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 8 }}>
          <div>
            <div style={labelStyle}>图表标题</div>
            <Input value={saveTitle} maxLength={50} onChange={(e) => setSaveTitle(e.target.value)} />
          </div>
          <div>
            <div style={labelStyle}>目标仪表板</div>
            <Select style={{ width: '100%' }} placeholder="新建仪表板" allowClear
              value={saveDashId ?? undefined} onChange={(v) => setSaveDashId(v ?? null)}
              options={dashboards.map((d) => ({ value: d.id, label: `${d.name}（${d.item_count ?? 0} 条）` }))} />
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>不选择则自动新建</div>
          </div>
        </Space>
      </Modal>
    </div>
  );
}

const sectionTitleStyle: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 4 };
const fieldItemStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', borderRadius: 6,
  cursor: 'grab', fontSize: 13, border: '1px solid transparent',
};
const axisTitleStyle: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 6, display: 'flex', alignItems: 'center' };
const slotBoxStyle: React.CSSProperties = {
  minHeight: 44, borderRadius: 8, border: '1.5px dashed #cbd5e1', background: '#fafbfd',
  padding: '6px 8px', display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center',
};
const slotTagStyle: React.CSSProperties = { fontSize: 12.5, padding: '2px 8px', background: '#eff6ff', borderColor: '#bfdbfe' };
const emptyHintStyle: React.CSSProperties = { fontSize: 12, color: '#cbd5e1', padding: '2px 4px' };
const labelStyle: React.CSSProperties = { fontSize: 12.5, color: '#64748b', marginBottom: 4 };
