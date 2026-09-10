/** 用量监控页：Token 用量总览 + 近 7 天趋势 + 节点分布 + 历史明细 */

import { useEffect, useState } from 'react';
import {
  App as AntApp, Button, Card, Col, Descriptions, Empty, InputNumber, Row, Spin, Table, Tag, Tooltip,
} from 'antd';
import {
  BarChartOutlined, DollarOutlined, FileTextOutlined, MessageOutlined,
  ReloadOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { api } from '../api/client';
import { EChart } from '../components/charts/EChart';
import type { EChartsOption } from 'echarts';
import type { Stats } from '../api/types';
import { useAppStore } from '../stores/appStore';

interface DailyStat {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface NodeStat {
  node: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface HistoryItem {
  run_id: number | null;
  session_id: number | null;
  node: string[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  created_at: string;
}

const NODE_LABELS: Record<string, string> = {
  parse_intent: '语义解析',
  generate_code: '代码生成',
  summarize: '结论整理',
  followup: '追问推荐',
  materialize: '数据缓存',
  skill: 'Skill 复用',
};

export function Usage() {
  const { message } = AntApp.useApp();

  const [stats, setStats] = useState<Stats | null>(null);
  const [daily, setDaily] = useState<DailyStat[]>([]);
  const [nodeStats, setNodeStats] = useState<NodeStat[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);

  const loadStats = async () => {
    try {
      const s = await api.get<Stats>('/api/stats');
      setStats(s);
      const summary = await api.get<{
        last_7_days: DailyStat[];
        by_node: NodeStat[];
      }>('/api/usage/summary');
      setDaily(summary.last_7_days);
      setNodeStats(summary.by_node);
    } catch { /* client.ts 已提示 */ }
  };

  const loadHistory = async (page = 1) => {
    setLoading(true);
    try {
      const r = await api.get<{ total: number; items: HistoryItem[] }>(`/api/usage/history?page=${page}&page_size=20`);
      setHistory(r.items);
      setHistoryTotal(r.total);
      setHistoryPage(page);
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadStats(); loadHistory(); }, [loadStats, loadHistory]);

  const fmtTokens = (n: number) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return String(n);
  };

  const fmtCost = (n: number) => `$${n.toFixed(4)}`;

  // 近 7 天趋势图
  const trendOption: EChartsOption | null = daily.length ? {
    tooltip: { trigger: 'axis' },
    legend: { data: ['输入 Token', '输出 Token', '费用 (USD)'] },
    grid: { left: 56, right: 60, top: 40, bottom: 48 },
    xAxis: { type: 'category', data: daily.map((d) => d.date) },
    yAxis: [
      { type: 'value', name: 'Token' },
      { type: 'value', name: 'USD', axisLabel: { formatter: '${value}' } },
    ],
    series: [
      { name: '输入 Token', type: 'bar', data: daily.map((d) => d.input_tokens), barMaxWidth: 24 },
      { name: '输出 Token', type: 'bar', data: daily.map((d) => d.output_tokens), barMaxWidth: 24 },
      { name: '费用 (USD)', type: 'line', yAxisIndex: 1, data: daily.map((d) => d.cost_usd), smooth: true },
    ],
  } : null;

  // 节点分布图
  const nodeOption: EChartsOption | null = nodeStats.length ? {
    tooltip: { trigger: 'item', formatter: '{b}: {c} tokens' },
    series: [{
      type: 'pie', radius: ['35%', '65%'], center: ['50%', '50%'],
      label: { formatter: '{b}\n{d}%' },
      data: nodeStats.map((n) => ({
        name: NODE_LABELS[n.node] || n.node,
        value: n.input_tokens + n.output_tokens,
      })),
    }],
  } : null;

  const historyColumns = [
    {
      title: '分析运行', dataIndex: 'run_id', width: 80,
      render: (v: number | null) => v ? `#${v}` : '-',
    },
    {
      title: '节点', dataIndex: 'node', width: 160,
      render: (nodes: string[]) => (
        <span>
          {nodes.map((n, i) => (
            <Tag key={n} color={i === 0 ? 'blue' : 'default'} style={{ marginRight: 4, fontSize: 11 }}>
              {NODE_LABELS[n] || n}
            </Tag>
          ))}
        </span>
      ),
    },
    {
      title: '输入 Token', dataIndex: 'input_tokens', width: 100,
      render: (v: number) => <span style={{ fontFamily: 'monospace' }}>{fmtTokens(v)}</span>,
    },
    {
      title: '输出 Token', dataIndex: 'output_tokens', width: 100,
      render: (v: number) => <span style={{ fontFamily: 'monospace' }}>{fmtTokens(v)}</span>,
    },
    {
      title: '总 Token', render: (_, r) => (
        <span style={{ fontWeight: 600, fontFamily: 'monospace' }}>{fmtTokens(r.input_tokens + r.output_tokens)}</span>
      ),
    },
    {
      title: '费用', dataIndex: 'cost_usd', width: 90,
      render: (v: number) => <span style={{ color: '#ea580c', fontFamily: 'monospace' }}>{fmtCost(v)}</span>,
    },
    {
      title: '时间', dataIndex: 'created_at', width: 170,
      render: (v: string) => v?.slice(0, 16),
    },
  ];

  const statCards = [
    { label: '累计输入 Token', value: fmtTokens(stats?.token_total_input ?? 0), icon: <FileTextOutlined />, color: '#5645D4', bg: '#ECE7F8' },
    { label: '累计输出 Token', value: fmtTokens(stats?.token_total_output ?? 0), icon: <MessageOutlined />, color: '#2A9D99', bg: '#f0fdfa' },
    { label: '总消耗 Token', value: fmtTokens((stats?.token_total_input ?? 0) + (stats?.token_total_output ?? 0)), icon: <ThunderboltOutlined />, color: '#7B5CF5', bg: '#f5f3ff' },
    { label: '预估费用', value: fmtCost(stats?.token_total_cost ?? 0), icon: <DollarOutlined />, color: '#ea580c', bg: '#fff7ed' },
    { label: '分析次数', value: String(stats?.token_runs ?? 0), icon: <BarChartOutlined />, color: '#dc2626', bg: '#fef2f2' },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>用量监控</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            DeepSeek API 调用追踪：Token 消耗、费用估算、分析频次
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <Button icon={<ReloadOutlined />} onClick={loadStats}>刷新</Button>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[14, 14]}>
        {statCards.map((s) => (
          <Col key={s.label} xs={12} sm={8} lg={4}>
            <Card size="small" style={{ borderRadius: 12 }} styles={{ body: { padding: '14px 16px' } }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{
                  width: 40, height: 40, borderRadius: 10, background: s.bg, color: s.color,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18,
                }}>{s.icon}</div>
                <div>
                  <div style={{ fontSize: 12, color: '#64748b' }}>{s.label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: '#0f172a', marginTop: 2 }}>{s.value}</div>
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 图表区 */}
      <Row gutter={[14, 14]} style={{ marginTop: 18 }}>
        <Col xs={24} lg={16}>
          <Card size="small" title="近 7 天 Token 用量趋势" style={{ borderRadius: 12 }}>
            <Spin spinning={!daily.length}>
              {trendOption ? (
                <EChart option={trendOption} height={320} />
              ) : (
                <Empty description="暂无数据" style={{ padding: '60px 0' }} />
              )}
            </Spin>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small" title="节点 Token 分布" style={{ borderRadius: 12 }}>
            <Spin spinning={!nodeStats.length}>
              {nodeOption ? (
                <EChart option={nodeOption} height={320} />
              ) : (
                <Empty description="暂无数据" style={{ padding: '60px 0' }} />
              )}
            </Spin>
          </Card>
        </Col>
      </Row>

      {/* 历史明细 */}
      <Card size="small" title="分析运行明细" style={{ borderRadius: 12, marginTop: 18 }}>
        <Table
          rowKey={(r) => `${r.run_id || r.id}-${r.created_at}`}
          loading={loading}
          columns={historyColumns}
          dataSource={history}
          pagination={{
            current: historyPage,
            total: historyTotal,
            pageSize: 20,
            onChange: (p) => loadHistory(p),
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
          }}
          locale={{ emptyText: <Empty description="暂无分析记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          size="small"
        />
      </Card>

      {/* 用量说明 */}
      <Card size="small" style={{ borderRadius: 12, marginTop: 18, borderStyle: 'dashed' }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>费用说明</div>
        <div style={{ fontSize: 13, color: '#64748b', lineHeight: 1.8 }}>
          <p>• 本页面费用为 DeepSeek API 预估费用，基于实际 Token 消耗和模型定价计算。</p>
          <p>• 费用单位：美元（USD）。DeepSeek Chat 定价：输入 $0.14/1M tokens，输出 $0.28/1M tokens。</p>
          <p>• 每次分析流程包含 4 次 LLM 调用：语义解析、代码生成、结论整理、追问推荐。</p>
          <p>• 数据每 60 秒自动刷新，也可手动点击「刷新」按钮更新。</p>
        </div>
      </Card>
    </div>
  );
}
