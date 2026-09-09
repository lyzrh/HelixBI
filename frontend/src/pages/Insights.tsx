/** 主动洞察页：扫描生成 + 列表筛选 + 详情抽屉（LLM 诊断 / 状态流转 / 固定仪表板） */

import { useEffect, useMemo, useState } from 'react';
import {
  App as AntApp, Button, Drawer, Empty, Input, Modal, Popconfirm, Popover,
  Progress, Select, Space, Spin, Switch, Tabs, Tag, Tooltip,
} from 'antd';
import {
  BulbOutlined, CheckOutlined, ClockCircleOutlined, DatabaseOutlined,
  DownloadOutlined, ReloadOutlined, SoundOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../api/client';
import type { DashboardInfo, InsightInfo, InsightScheduleInfo } from '../api/types';
import { useAppStore } from '../stores/appStore';

/** 后端 insight_engine.RULE_LABELS 的前端映射 */
const RULE_LABELS: Record<string, string> = {
  spike: '指标突变', streak: '连续趋势', outlier: '异常值',
  topn_shift: '头部份额变化', quality: '数据质量', threshold: '业务阈值越界',
  gap: '时间断档',
};

const SEVERITY: Record<string, { color: string; label: string; bg: string; border: string }> = {
  critical: { color: '#dc2626', label: '严重', bg: '#fef2f2', border: '#fecaca' },
  warning: { color: '#ea580c', label: '警告', bg: '#fff7ed', border: '#fed7aa' },
  info: { color: '#2563eb', label: '提示', bg: '#eff6ff', border: '#bfdbfe' },
};

const STATUS_TAB = [
  { key: '', label: '全部' },
  { key: 'new', label: '新发现' },
  { key: 'read', label: '已读' },
  { key: 'resolved', label: '已解决' },
];

const INTERVAL_OPTIONS = [
  { value: 15, label: '每 15 分钟' },
  { value: 30, label: '每 30 分钟' },
  { value: 60, label: '每 1 小时' },
  { value: 180, label: '每 3 小时' },
  { value: 360, label: '每 6 小时' },
  { value: 720, label: '每 12 小时' },
  { value: 1440, label: '每天' },
];

function intervalLabel(minutes: number): string {
  const hit = INTERVAL_OPTIONS.find((o) => o.value === minutes);
  if (hit) return hit.label.replace('每 ', '');
  return minutes >= 60 ? `${Math.round(minutes / 60)} 小时` : `${minutes} 分钟`;
}

export function Insights() {
  const { message } = AntApp.useApp();

  const [insights, setInsights] = useState<InsightInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusTab, setStatusTab] = useState('');
  const [dsFilter, setDsFilter] = useState<number | null>(null);
  const dataSources = useAppStore((s) => s.dataSources);
  const loadDataSources = useAppStore((s) => s.loadDataSources);

  // 扫描
  const [scanDsIds, setScanDsIds] = useState<number[]>([]);
  const [scanning, setScanning] = useState(false);

  // 定时扫描
  const [schedule, setSchedule] = useState<InsightScheduleInfo | null>(null);
  const [schedSaving, setSchedSaving] = useState(false);
  const [autoRunning, setAutoRunning] = useState(false);

  // 详情抽屉
  const [detail, setDetail] = useState<InsightInfo | null>(null);
  const [reporting, setReporting] = useState(false);

  // 固定到仪表板
  const [pinTarget, setPinTarget] = useState<InsightInfo | null>(null);
  const [dashboards, setDashboards] = useState<DashboardInfo[]>([]);
  const [dashChoice, setDashChoice] = useState<number | 'new'>(-1);
  const [newDashName, setNewDashName] = useState('');

  /** 一次性加载全量洞察，筛选在前端完成（概览统计不受筛选影响） */
  const reload = async () => {
    setLoading(true);
    try {
      setInsights(await api.get<InsightInfo[]>('/api/insights'));
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDataSources();
    api.get<InsightScheduleInfo>('/api/insights/schedule').then(setSchedule).catch(() => undefined);
  }, [loadDataSources]);

  useEffect(() => {
    reload();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /** 前端筛选：状态 Tab + 数据源 */
  const filtered = useMemo(
    () => insights.filter((i) =>
      (!statusTab || i.status === statusTab) && (!dsFilter || i.data_source_id === dsFilter)),
    [insights, statusTab, dsFilter],
  );

  /** 全量统计（概览卡 + Tab 徽标） */
  const counts = useMemo(() => {
    const by: Record<string, number> = { new: 0, read: 0, resolved: 0 };
    const sev: Record<string, number> = { critical: 0, warning: 0, info: 0 };
    insights.forEach((i) => {
      if (by[i.status] !== undefined) by[i.status] += 1;
      if (sev[i.severity] !== undefined) sev[i.severity] += 1;
    });
    return { ...by, sev };
  }, [insights]);

  const doScan = async () => {
    if (!scanDsIds.length) { message.warning('请选择要扫描的数据源'); return; }
    setScanning(true);
    try {
      const found = await api.post<{ total: number; new: number }[]>('/api/insights/generate', { data_source_ids: scanDsIds });
      const total = found.reduce((s, r) => s + r.total, 0);
      const fresh = found.reduce((s, r) => s + r.new, 0);
      message.success(`扫描完成：${total} 条洞察（新增 ${fresh} 条）`);
      setStatusTab('');
      reload();
      api.get<InsightScheduleInfo>('/api/insights/schedule').then(setSchedule).catch(() => undefined);
    } catch { /* client.ts 已提示 */ } finally {
      setScanning(false);
    }
  };

  // ---- 定时扫描 ----

  const toggleSchedule = async (enabled: boolean) => {
    setSchedSaving(true);
    try {
      setSchedule(await api.put<InsightScheduleInfo>('/api/insights/schedule', { enabled }));
      message.success(enabled ? '已开启定时扫描（30 秒内执行首轮）' : '已关闭定时扫描');
    } catch { /* client.ts 已提示 */ } finally {
      setSchedSaving(false);
    }
  };

  const toggleAutoDiagnose = async (on: boolean) => {
    setSchedSaving(true);
    try {
      setSchedule(await api.put<InsightScheduleInfo>('/api/insights/schedule', { auto_diagnose: on }));
      message.success(on ? '已开启自动诊断：扫描发现的无报告告警洞察将自动生成 LLM 诊断'
        : '已关闭自动诊断');
    } catch { /* client.ts 已提示 */ } finally {
      setSchedSaving(false);
    }
  };

  const changeInterval = async (minutes: number) => {
    setSchedSaving(true);
    try {
      setSchedule(await api.put<InsightScheduleInfo>('/api/insights/schedule', { interval_minutes: minutes }));
    } catch { /* client.ts 已提示 */ } finally {
      setSchedSaving(false);
    }
  };

  const runAutoScan = async () => {
    setAutoRunning(true);
    try {
      const r = await api.post<{ scanned: number; total: number; new: number; diagnosed: number }>('/api/insights/schedule/run');
      message.success(`全量扫描完成：${r.scanned} 个数据源 · ${r.total} 条洞察（新增 ${r.new} 条${r.diagnosed ? `，自动诊断 ${r.diagnosed} 条` : ''}）`);
      setSchedule(await api.get<InsightScheduleInfo>('/api/insights/schedule'));
      setStatusTab('');
      reload();
    } catch { /* client.ts 已提示 */ } finally {
      setAutoRunning(false);
    }
  };

  const setInsightStatus = async (i: InsightInfo, status: InsightInfo['status']) => {
    try {
      await api.patch(`/api/insights/${i.id}`, { status });
      setInsights((list) => list.map((x) => (x.id === i.id ? { ...x, status } : x)));
      setDetail((d) => (d && d.id === i.id ? { ...d, status } : d));
    } catch { /* client.ts 已提示 */ }
  };

  const makeReport = async () => {
    if (!detail) return;
    setReporting(true);
    try {
      const r = await api.post<{ report: string }>(`/api/insights/${detail.id}/report`);
      setDetail({ ...detail, report: r.report });
      message.success('诊断报告已生成');
    } catch { /* client.ts 已提示（如未配置 LLM） */ } finally {
      setReporting(false);
    }
  };

  const openPin = (i: InsightInfo) => {
    setPinTarget(i);
    api.get<DashboardInfo[]>('/api/dashboards').then((list) => {
      setDashboards(list);
      setDashChoice(list.length ? list[0].id : 'new');
      setNewDashName('');
    });
  };

  const pinOk = async () => {
    if (!pinTarget) return;
    try {
      let did = dashChoice;
      if (did === 'new') {
        if (!newDashName.trim()) { message.warning('请填写新仪表板名称'); return; }
        const d = await api.post<DashboardInfo>('/api/dashboards', { name: newDashName.trim(), description: '' });
        did = d.id;
      }
      await api.post(`/api/dashboards/${did}/items`, {
        type: 'insight', title: pinTarget.title,
        payload: { insight_id: pinTarget.id },
        source_run_id: null,
      });
      message.success('已固定到仪表板');
      setPinTarget(null);
    } catch { /* client.ts 已提示 */ }
  };

  const sev = (i: InsightInfo) => SEVERITY[i.severity] ?? SEVERITY.info;

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>主动洞察</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            规则引擎主动扫描数据：突变 / 趋势 / 异常值 / 阈值越界，配合 LLM 生成经营诊断
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <DatabaseOutlined style={{ color: '#64748b' }} />
        <Select mode="multiple" style={{ minWidth: 260, maxWidth: 420 }} placeholder="选择要扫描的数据源"
          value={scanDsIds} onChange={setScanDsIds} maxTagCount="responsive" disabled={scanning}
          options={dataSources.map((d) => ({ value: d.id, label: d.name }))} />
        <Button type="primary" icon={<ThunderboltOutlined />} loading={scanning} onClick={doScan}>
          {scanning ? '扫描中…' : '扫描洞察'}
        </Button>
        <Tooltip title="导出当前筛选的洞察清单为 CSV（Excel 可直接打开）">
          <Button icon={<DownloadOutlined />} disabled={!insights.length}
            onClick={() => { window.open(`/api/insights/export${statusTab ? `?status=${statusTab}` : ''}`, '_blank'); }}>
            导出 CSV
          </Button>
        </Tooltip>
        <Popover
          trigger="click"
          placement="bottomRight"
          title={(
            <span style={{ fontSize: 13.5 }}>
              <ClockCircleOutlined style={{ marginRight: 6, color: '#2563eb' }} />
              定时扫描
            </span>
          )}
          content={(
            <div style={{ width: 300 }}>
              <div style={schedRowStyle}>
                <span style={{ fontSize: 13 }}>自动扫描全部数据源</span>
                <Switch size="small" loading={schedSaving}
                  checked={schedule?.enabled ?? false}
                  onChange={toggleSchedule} />
              </div>
              <div style={schedRowStyle}>
                <span style={{ fontSize: 13 }}>扫描间隔</span>
                <Select size="small" style={{ width: 120 }} disabled={schedSaving}
                  value={schedule?.interval_minutes ?? 60} onChange={changeInterval}
                  options={INTERVAL_OPTIONS} />
              </div>
              <div style={{ ...schedRowStyle, alignItems: 'flex-start' }}>
                <span style={{ fontSize: 13 }}>
                  新告警自动诊断
                  <div style={{ fontSize: 11.5, color: '#cbd5e1', fontWeight: 400, marginTop: 2 }}>
                    告警/严重洞察自动生成 LLM 诊断（每轮最多 3 条）
                  </div>
                </span>
                <Switch size="small" loading={schedSaving}
                  checked={schedule?.auto_diagnose ?? false}
                  onChange={toggleAutoDiagnose} />
              </div>
              <div style={{ ...schedInfoStyle, borderTop: '1px dashed #e2e8f0', paddingTop: 8, marginTop: 4 }}>
                {schedule?.last_run_at
                  ? <>上次：{schedule.last_run_at} · {schedule.last_result?.scanned ?? 0} 源 / {schedule.last_result?.total ?? 0} 条（新增 {schedule.last_result?.new ?? 0}
                    {schedule.last_result?.diagnosed ? `，自动诊断 ${schedule.last_result.diagnosed}` : ''}）</>
                  : '尚未执行过扫描'}
                <br />
                {schedule?.enabled && schedule.next_run_at
                  ? <>下次：{schedule.next_run_at}</>
                  : '定时未开启'}
              </div>
              <Button size="small" block style={{ marginTop: 10 }} icon={<ThunderboltOutlined />}
                loading={autoRunning} onClick={runAutoScan}>
                立即全量扫描
              </Button>
              <div style={{ fontSize: 11.5, color: '#cbd5e1', marginTop: 8, lineHeight: 1.6 }}>
                规则引擎本地计算，不消耗模型额度；已读/已解决状态在扫描间保留
              </div>
            </div>
          )}
        >
          <Tooltip title="定时扫描设置">
            <Button icon={<ClockCircleOutlined />}
              style={schedule?.enabled ? { borderColor: '#2563eb', color: '#2563eb' } : undefined}>
              {schedule?.enabled ? `定时·${intervalLabel(schedule?.interval_minutes ?? 60)}` : '定时扫描'}
            </Button>
          </Tooltip>
        </Popover>
      </div>

      {/* 概览统计卡（参考 FineDataLink 定时任务运维概览：总数 + 环形状态分布） */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ ...overviewCardStyle, flex: '1 1 220px' }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: 'linear-gradient(135deg,#3b82f6,#6366f1)', color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 21,
          }}><SoundOutlined /></div>
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>洞察总数</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{insights.length}</div>
            <div style={{ fontSize: 11.5, color: '#94a3b8' }}>
              严重 {counts.sev.critical} · 警告 {counts.sev.warning} · 提示 {counts.sev.info}
            </div>
          </div>
        </div>
        <div style={{ ...overviewCardStyle, flex: '1 1 160px' }}>
          <Progress
            type="circle" size={58}
            percent={insights.length ? Math.round((counts.new / insights.length) * 100) : 0}
            strokeColor="#2563eb" format={() => `${counts.new}`}
          />
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>待处理（新发现）</div>
            <div style={{ fontSize: 13, color: '#2563eb', fontWeight: 600 }}>
              {insights.length ? `${Math.round((counts.new / insights.length) * 100)}%` : '-'}
            </div>
          </div>
        </div>
        <div style={{ ...overviewCardStyle, flex: '1 1 160px' }}>
          <Progress
            type="circle" size={58}
            percent={insights.length ? Math.round((counts.resolved / insights.length) * 100) : 0}
            strokeColor="#16a34a" format={() => `${counts.resolved}`}
          />
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>已解决</div>
            <div style={{ fontSize: 11.5, color: '#94a3b8' }}>已读 {counts.read} 条</div>
          </div>
        </div>
        <div style={{ ...overviewCardStyle, flex: '1 1 200px', borderColor: counts.sev.critical ? '#fecaca' : '#eef2f7' }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: counts.sev.critical ? '#fef2f2' : '#f8fafc',
            color: counts.sev.critical ? '#dc2626' : '#94a3b8',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 21,
          }}><ThunderboltOutlined /></div>
          <div>
            <div style={{ fontSize: 12.5, color: '#64748b' }}>严重告警</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: counts.sev.critical ? '#dc2626' : '#1e293b' }}>
              {counts.sev.critical}
            </div>
            <div style={{ fontSize: 11.5, color: '#94a3b8' }}>
              {counts.sev.critical ? '需要优先关注' : '暂无严重告警'}
            </div>
          </div>
        </div>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #eef2f7', padding: '4px 16px 12px' }}>
        <Tabs
          activeKey={statusTab}
          onChange={(k) => setStatusTab(k)}
          items={STATUS_TAB.map((t) => ({
            key: t.key,
            label: (
              <span>
                {t.label}
                {t.key && counts[t.key] > 0 && (
                  <span style={{ marginLeft: 6, fontSize: 11, background: t.key === 'new' ? '#2563eb' : '#94a3b8', color: '#fff', borderRadius: 8, padding: '0 6px' }}>
                    {counts[t.key]}
                  </span>
                )}
              </span>
            ),
          }))}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          <Select style={{ width: 200 }} placeholder="按数据源筛选" allowClear
            value={dsFilter} onChange={(v) => setDsFilter(v ?? null)}
            options={dataSources.map((d) => ({ value: d.id, label: d.name }))} />
        </div>

        <Spin spinning={loading}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {filtered.map((i) => {
              const s = sev(i);
              return (
                <div key={i.id}
                  style={{ border: `1px solid ${s.border}`, background: s.bg, borderRadius: 10, padding: '12px 16px', cursor: 'pointer' }}
                  onClick={() => setDetail(i)}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <Tag color={s.color} style={{ margin: 0, color: '#fff' }}>{s.label}</Tag>
                    <span style={{ fontWeight: 600, fontSize: 13.5, color: '#1e293b' }}>{i.title}</span>
                    <Tag style={{ margin: 0, fontSize: 11.5 }}>{RULE_LABELS[i.rule_id] ?? i.rule_id}</Tag>
                    {i.status === 'new' && <Tag color="blue" style={{ margin: 0, fontSize: 11 }}>新</Tag>}
                    {i.status === 'resolved' && (
                      <Tag icon={<CheckOutlined />} color="success" style={{ margin: 0, fontSize: 11 }}>已解决</Tag>
                    )}
                    <div style={{ flex: 1 }} />
                    <span style={{ fontSize: 12, color: '#94a3b8' }}>
                      {i.data_source_name} · {i.created_at?.slice(5, 16)}
                    </span>
                  </div>
                  <div style={{ fontSize: 13, color: '#475569', marginTop: 6 }}>{i.detail}</div>
                </div>
              );
            })}
          </div>
          {!loading && !filtered.length && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              style={{ padding: '32px 0' }}
              description="暂无洞察：选择数据源点击「扫描洞察」"
            />
          )}
        </Spin>
      </div>

      {/* 详情抽屉 */}
      <Drawer
        open={!!detail}
        onClose={() => setDetail(null)}
        width={560}
        title={detail ? (
          <Space>
            <SoundOutlined style={{ color: sev(detail).color }} />
            <span>洞察详情</span>
          </Space>
        ) : '洞察详情'}
      >
        {detail && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <Tag color={sev(detail).color} style={{ color: '#fff' }}>{sev(detail).label}</Tag>
              <Tag>{RULE_LABELS[detail.rule_id] ?? detail.rule_id}</Tag>
              <Tag color="geekblue">{detail.data_source_name}</Tag>
              <span style={{ fontSize: 12, color: '#94a3b8', marginLeft: 'auto' }}>
                <ClockCircleOutlined /> {detail.created_at?.slice(0, 16)}
              </span>
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>{detail.title}</div>
            <div style={{ fontSize: 13.5, color: '#475569', lineHeight: 1.8, marginBottom: 16 }}>{detail.detail}</div>

            <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>数字证据</div>
            <pre style={{
              background: '#0f172a', color: '#e2e8f0', padding: 12, borderRadius: 8,
              fontSize: 12, overflow: 'auto', maxHeight: 220,
            }}>
              <code>{JSON.stringify(detail.evidence, null, 2)}</code>
            </pre>

            <div style={{ margin: '16px 0 8px', display: 'flex', alignItems: 'center', gap: 8 }}>
              <BulbOutlined style={{ color: '#7c3aed' }} />
              <span style={{ fontSize: 13.5, fontWeight: 600 }}>LLM 经营诊断</span>
              {!detail.report && (
                <Tooltip title="调用大模型：现象 → 数据证据 → 可能原因 → 建议动作">
                  <Button size="small" icon={<ThunderboltOutlined />} loading={reporting} onClick={makeReport}>
                    生成诊断
                  </Button>
                </Tooltip>
              )}
            </div>
            {detail.report ? (
              <div className="answer-md" style={{
                background: '#faf5ff', border: '1px solid #e9d5ff', borderRadius: 8, padding: '12px 16px',
              }}>
                <Markdown remarkPlugins={[remarkGfm]}>{detail.report}</Markdown>
              </div>
            ) : (
              <div style={{ fontSize: 12.5, color: '#94a3b8', padding: '8px 0' }}>
                尚未生成诊断报告（需配置 LLM API Key）
              </div>
            )}

            <div style={{ borderTop: '1px solid #eef2f7', marginTop: 20, paddingTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {detail.status === 'new' && (
                <Button size="small" onClick={() => setInsightStatus(detail, 'read')}>标记已读</Button>
              )}
              {detail.status !== 'resolved' && (
                <Button size="small" type="primary" ghost icon={<CheckOutlined />}
                  onClick={() => setInsightStatus(detail, 'resolved')}>标记已解决</Button>
              )}
              {detail.status !== 'new' && (
                <Popconfirm title="重新标记为新发现？" onConfirm={() => setInsightStatus(detail, 'new')}>
                  <Button size="small" icon={<ReloadOutlined />}>重新打开</Button>
                </Popconfirm>
              )}
              <div style={{ flex: 1 }} />
              <Button size="small" icon={<CheckOutlined />} onClick={() => openPin(detail)}>固定到仪表板</Button>
            </div>
          </div>
        )}
      </Drawer>

      {/* 固定到仪表板弹窗 */}
      <Modal
        open={!!pinTarget} title="固定洞察到仪表板"
        onOk={pinOk} onCancel={() => setPinTarget(null)} okText="固定" cancelText="取消"
      >
        <div style={{ marginBottom: 12, fontSize: 13, color: '#475569' }}>
          将以文本卡片形式固定：<b>{pinTarget?.title}</b>
        </div>
        <Select
          style={{ width: '100%' }} value={dashChoice === -1 ? undefined : dashChoice}
          onChange={(v) => setDashChoice(v as number | 'new')}
          options={[
            ...dashboards.map((d) => ({ value: d.id, label: `${d.name}（${d.item_count ?? 0} 项）` })),
            { value: 'new', label: '＋ 新建仪表板…' },
          ]}
          placeholder="选择或新建仪表板" />
        {dashChoice === 'new' && (
          <Input style={{ marginTop: 8 }} placeholder="新仪表板名称"
            value={newDashName} onChange={(e) => setNewDashName(e.target.value)} />
        )}
      </Modal>
    </div>
  );
}

const schedRowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 0',
};
const schedInfoStyle: React.CSSProperties = {
  fontSize: 12, color: '#64748b', lineHeight: 1.8,
};
const overviewCardStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
  background: '#fff', borderRadius: 12, border: '1px solid #eef2f7',
  boxShadow: '0 1px 3px rgba(15,23,42,0.04)',
};
