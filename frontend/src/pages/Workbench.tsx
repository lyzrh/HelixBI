/** 工作台首页（Premium SaaS）：品牌渐变 Hero + 提问直达对话 + 快捷入口 + 统计 + 趋势/热门/最近 */

import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Col, Input, Row, Tabs, Tag } from 'antd';
import {
  ArrowRightOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  MessageOutlined, PieChartOutlined, RightOutlined, SendOutlined,
  SoundOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';
import { EChart } from '../components/charts/EChart';
import { useT, type StrKey } from '../i18n';

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const QUICK_ACTIONS = [
  { key: '/chat', icon: <MessageOutlined />, title: '数据分析', desc: '自然语言提问，Agent 自动分析',
    color: '#5645D4', bg: '#EFECFB' },
  { key: '/dashboards', icon: <DashboardOutlined />, title: '创建仪表盘', desc: '沉淀可复分析看板',
    color: '#2F6BFF', bg: '#EFF4FF' },
  { key: '/datasources', icon: <DatabaseOutlined />, title: '数据源管理', desc: '连接你的数据',
    color: '#16A34A', bg: '#EFFBF2' },
  { key: '/insights', icon: <SoundOutlined />, title: '智能洞察', desc: '发现业务机会',
    color: '#DD5B00', bg: '#FFF3E8' },
];

const MODULES = [
  { key: '/chat', icon: <MessageOutlined />, titleKey: 'wb.mChat' as StrKey, descKey: 'wb.mChatDesc' as StrKey, color: '#5645D4', bg: '#EFECFB' },
  { key: '/explore', icon: <PieChartOutlined />, titleKey: 'wb.mExplore' as StrKey, descKey: 'wb.mExploreDesc' as StrKey, color: '#2A9D99', bg: '#D9F0EE' },
  { key: '/agents', icon: <ThunderboltOutlined />, titleKey: 'wb.mAgents' as StrKey, descKey: 'wb.mAgentsDesc' as StrKey, color: '#7B5CF5', bg: '#F1EDFC' },
  { key: '/skills', icon: <ExperimentOutlined />, titleKey: 'wb.mSkills' as StrKey, descKey: 'wb.mSkillsDesc' as StrKey, color: '#B7791F', bg: '#FBF3DC' },
  { key: '/insights', icon: <SoundOutlined />, titleKey: 'wb.mInsights' as StrKey, descKey: 'wb.mInsightsDesc' as StrKey, color: '#DD5B00', bg: '#FFF3E8' },
  { key: '/dashboards', icon: <DashboardOutlined />, titleKey: 'wb.mDash' as StrKey, descKey: 'wb.mDashDesc' as StrKey, color: '#C2255C', bg: '#FDECF3' },
];

const SCENARIOS = [
  {
    titleKey: 'wb.sc1' as StrKey, icon: <PieChartOutlined />, to: '/agents',
    color: '#2A9D99', bg: '#D9F0EE',
    descKey: 'wb.sc1Desc' as StrKey,
    points: ['wb.sc1p1', 'wb.sc1p2', 'wb.sc1p3'] as StrKey[],
  },
  {
    titleKey: 'wb.sc2' as StrKey, icon: <ThunderboltOutlined />, to: '/agents',
    color: '#5645D4', bg: '#EFECFB',
    descKey: 'wb.sc2Desc' as StrKey,
    points: ['wb.sc2p1', 'wb.sc2p2', 'wb.sc2p3'] as StrKey[],
  },
  {
    titleKey: 'wb.sc3' as StrKey, icon: <SoundOutlined />, to: '/insights',
    color: '#DD5B00', bg: '#FFF3E8',
    descKey: 'wb.sc3Desc' as StrKey,
    points: ['wb.sc3p1', 'wb.sc3p2', 'wb.sc3p3'] as StrKey[],
  },
];

const GUIDE_STEPS = [
  { titleKey: 'wb.g1' as StrKey, descKey: 'wb.g1Desc' as StrKey },
  { titleKey: 'wb.g2' as StrKey, descKey: 'wb.g2Desc' as StrKey },
  { titleKey: 'wb.g3' as StrKey, descKey: 'wb.g3Desc' as StrKey },
  { titleKey: 'wb.g4' as StrKey, descKey: 'wb.g4Desc' as StrKey },
];

interface TrendPoint { [k: string]: string | number }
interface AgentInfo {
  id: number; name: string; enabled: boolean;
  recommended_questions: string[];
}
interface SessionRow { id: number; title: string; updated_at: string }

function gotoAsk(question: string) {
  // 横幅提问 → 对话页预填（chatStore 输入为页面本地 state，走一次性 sessionStorage 交接）
  sessionStorage.setItem('helix_prefill_q', question);
  window.location.href = '/chat';
}

export function Workbench() {
  const navigate = useNavigate();
  const t = useT();
  const stats = useAppStore((s) => s.stats);
  const loadStats = useAppStore((s) => s.loadStats);
  const health = useAppStore((s) => s.health);
  const context = useAuthStore((s) => s.context);
  const [question, setQuestion] = useState('');
  const [trend, setTrend] = useState<{ name: string; points: TrendPoint[] } | null>(null);
  const [hotQuestions, setHotQuestions] = useState<string[]>([]);
  const [recentSessions, setRecentSessions] = useState<SessionRow[]>([]);

  useEffect(() => { loadStats(); }, [loadStats]);

  useEffect(() => {
    // 热门分析：场景 Agent 的推荐问题（真实数据）
    api.get<AgentInfo[]>('/api/agents').then((rows) => {
      const qs = rows.filter((a) => a.enabled).flatMap((a) => a.recommended_questions || []);
      setHotQuestions(qs.slice(0, 5));
    }).catch(() => setHotQuestions([]));
    // 最近使用：最近分析会话
    api.get<SessionRow[]>('/api/sessions').then((rows) => {
      setRecentSessions(rows.slice(0, 5));
    }).catch(() => setRecentSessions([]));
    // 数据趋势：第一个数据源按月汇总（本地零 token 计算真实数据）
    api.get<{ id: number; name: string }[]>('/api/datasources').then(async (rows) => {
      if (!rows.length) return;
      const first = rows[0];
      const resp = await api.post<{ rows: TrendPoint[] }>(
        `/api/explore/query`,
        { data_source_id: first.id, dimensions: ['订单日期'], date_grain: 'month',
          metrics: [{ field: '销售额', agg: 'sum' }], sort_field: '订单日期', limit: 8 });
      if (resp.rows?.length) setTrend({ name: first.name, points: resp.rows });
    }).catch(() => setTrend(null));
  }, []);

  const trendOption = useMemo<EChartsOption | null>(() => {
    if (!trend?.points.length) return null;
    const xs = trend.points.map((p) => String(p['订单日期'] ?? ''));
    const ys = trend.points.map((p) => Number(p['销售额'] ?? 0));
    return {
      grid: { left: 8, right: 12, top: 24, bottom: 4, containLabel: true },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: xs, axisLine: { lineStyle: { color: '#E5E9F0' } },
               axisLabel: { color: '#98A2B3', fontSize: 11 }, axisTick: { show: false } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#F2F4F8' } },
               axisLabel: { color: '#98A2B3', fontSize: 11,
                            formatter: (v: number) => formatNumber(v) } },
      series: [{
        type: 'line', data: ys, smooth: true, symbolSize: 6,
        lineStyle: { width: 2.5, color: '#5645D4' },
        itemStyle: { color: '#5645D4' },
        areaStyle: { color: 'rgba(86,69,212,0.10)' },
      }],
    };
  }, [trend]);

  const statCards = [
    { label: t('wb.statDs'), value: stats?.data_sources ?? 0, hint: t('wb.statDsHint') },
    { label: t('wb.statSessions'), value: stats?.sessions ?? 0, hint: t('wb.statSessionsHint') },
    {
      label: t('wb.statSuccess'), value: stats?.runs ? `${Math.round(((stats.runs_ok ?? 0) / stats.runs) * 100)}%` : '-',
      hint: `${stats?.runs ?? 0} ${t('wb.runsUnit')}`,
    },
    { label: t('wb.statSkills'), value: stats?.skills ?? 0, hint: t('wb.statSkillsHint') },
    { label: t('wb.statInsights'), value: stats?.insights_new ?? 0, hint: t('wb.statInsightsHint') },
    { label: t('wb.statDash'), value: stats?.dashboards ?? 0, hint: t('wb.statDashHint') },
    { label: t('wb.statTokenIn'), value: formatNumber(stats?.token_total_input ?? 0), hint: t('wb.statTokenInHint') },
    { label: t('wb.statTokenOut'), value: formatNumber(stats?.token_total_output ?? 0), hint: t('wb.statTokenOutHint') },
    { label: t('wb.statCost'), value: `¥${(stats?.token_total_cost ?? 0).toFixed(4)}`, hint: t('wb.statCostHint', { n: stats?.token_runs ?? 0 }) },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      {/* Hero：品牌渐变 + 个性化问候 + 提问直达（全站唯一大面积渐变） */}
      <div className="brand-gradient" style={{
        borderRadius: 16, padding: '30px 36px 30px', color: '#fff', position: 'relative', overflow: 'hidden',
        boxShadow: '0 10px 30px rgba(47,27,102,0.28)',
      }}>
        <div style={{
          position: 'absolute', right: -70, top: -90, width: 340, height: 340, borderRadius: '50%',
          background: 'rgba(255,255,255,0.07)',
        }} />
        <div style={{
          position: 'absolute', right: 130, bottom: -120, width: 260, height: 260, borderRadius: '50%',
          background: 'rgba(255,255,255,0.05)',
        }} />
        <div style={{ position: 'relative' }}>
          <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: -0.3 }}>
            你好，{context?.display_name || context?.username || '分析师'} 👋
          </div>
          <div style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.80)', marginTop: 6 }}>
            用 AI 让数据分析更简单、更智能 —— Turn Data into Decisions.
          </div>
          <div style={{
            marginTop: 18, display: 'flex', gap: 10, alignItems: 'center',
            background: 'rgba(255,255,255,0.96)', borderRadius: 12, padding: '6px 6px 6px 16px',
            maxWidth: 640, boxShadow: '0 4px 14px rgba(10,21,48,0.18)',
          }}>
            <Input
              variant="borderless"
              placeholder="比如：分析上个月的销售趋势，帮我生成图表"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onPressEnter={() => question.trim() && gotoAsk(question.trim())}
              style={{ fontSize: 13.5 }}
            />
            <Button type="primary" icon={<SendOutlined />} className="brand-gradient-soft"
              style={{ border: 'none' }}
              onClick={() => question.trim() && gotoAsk(question.trim())} />
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
            <Tag style={{ background: 'rgba(255,255,255,0.12)', color: '#fff', border: 'none', borderRadius: 999 }}>
              {t('wb.chipModel')}：{health?.llm_configured ? health.model : t('status.notConfigured')}
            </Tag>
            <Tag style={{ background: 'rgba(255,255,255,0.12)', color: '#fff', border: 'none', borderRadius: 999 }}>
              {t('wb.chipSandbox')}：{health?.sandbox ? t('status.online') : t('status.offline')}
            </Tag>
          </div>
        </div>
      </div>

      {/* 快捷入口 */}
      <Row gutter={[14, 14]} style={{ marginTop: 16 }}>
        {QUICK_ACTIONS.map((a) => (
          <Col key={a.key} xs={12} lg={6}>
            <Card hoverable className="saas-card" style={{ borderRadius: 14, border: '1px solid #EEF1F6' }}
              onClick={() => navigate(a.key)}
              styles={{ body: { padding: 16, display: 'flex', alignItems: 'center', gap: 12 } }}>
              <div style={{
                width: 42, height: 42, borderRadius: 11, background: a.bg, color: a.color,
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 19,
                flexShrink: 0,
              }}>{a.icon}</div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: '#101828' }}>{a.title}</div>
                <div style={{ fontSize: 12, color: '#98A2B3', marginTop: 2,
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.desc}
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 统计 */}
      <Row gutter={[14, 14]} style={{ marginTop: 16 }}>
        {statCards.map((s) => (
          <Col key={s.label} xs={12} sm={8} lg={6}>
            <Card size="small" style={{ borderRadius: 14, border: '1px solid #EEF1F6' }}
              styles={{ body: { padding: '14px 18px' } }}>
              <div style={{ fontSize: 12.5, color: '#475467' }}>{s.label}</div>
              <div style={{ fontSize: 23, fontWeight: 700, marginTop: 4, color: '#101828', letterSpacing: -0.3 }}>
                {s.value}
              </div>
              <div style={{ fontSize: 11.5, color: '#98A2B3', marginTop: 2 }}>{s.hint}</div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 趋势 / 热门分析 / 最近使用 */}
      <Row gutter={[14, 14]} style={{ marginTop: 16 }}>
        {trend && trendOption && (
          <Col xs={24} lg={12}>
            <Card style={{ borderRadius: 14, border: '1px solid #EEF1F6', height: '100%' }}
              styles={{ body: { padding: '16px 18px' } }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontSize: 14.5, fontWeight: 600 }}>销售趋势</div>
                <div style={{ fontSize: 12, color: '#98A2B3' }}>{trend.name}</div>
              </div>
              <div style={{ marginTop: 8 }}>
                <EChart option={trendOption} height={218} />
              </div>
            </Card>
          </Col>
        )}
        <Col xs={24} lg={trend ? 6 : 12}>
          <Card style={{ borderRadius: 14, border: '1px solid #EEF1F6', height: '100%' }}
            styles={{ body: { padding: '16px 18px' } }}>
            <div style={{ fontSize: 14.5, fontWeight: 600, marginBottom: 10 }}>热门分析</div>
            {hotQuestions.length ? hotQuestions.map((q) => (
              <div key={q} className="saas-card" onClick={() => gotoAsk(q)}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                         gap: 8, padding: '8px 10px', borderRadius: 9, cursor: 'pointer',
                         background: '#F9FAFC', marginBottom: 8 }}>
                <span style={{ fontSize: 12.5, color: '#344054',
                               overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q}</span>
                <RightOutlined style={{ fontSize: 10, color: '#C2C7D0', flexShrink: 0 }} />
              </div>
            )) : (
              <div style={{ fontSize: 12.5, color: '#98A2B3' }}>暂无推荐问题</div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={trend ? 6 : 12}>
          <Card style={{ borderRadius: 14, border: '1px solid #EEF1F6', height: '100%' }}
            styles={{ body: { padding: '16px 18px' } }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
              <div style={{ fontSize: 14.5, fontWeight: 600 }}>最近使用</div>
              <Button type="link" size="small" style={{ padding: 0, fontSize: 12 }}
                onClick={() => navigate('/chat')}>查看全部 →</Button>
            </div>
            {recentSessions.length ? recentSessions.map((s) => (
              <div key={s.id} className="saas-card" onClick={() => navigate(`/chat/${s.id}`)}
                style={{ padding: '8px 10px', borderRadius: 9, cursor: 'pointer',
                         background: '#F9FAFC', marginBottom: 8 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500, color: '#344054',
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.title}
                </div>
                <div style={{ fontSize: 11, color: '#98A2B3', marginTop: 2 }}>{s.updated_at}</div>
              </div>
            )) : (
              <div style={{ fontSize: 12.5, color: '#98A2B3' }}>暂无会话，去发起第一次分析吧</div>
            )}
          </Card>
        </Col>
      </Row>

      {/* 功能模块（Tab 化：模块 / 价值场景 / 使用指南） */}
      <div style={{ fontSize: 15, fontWeight: 600, margin: '26px 0 4px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 3, height: 15, borderRadius: 2, background: '#5645D4', display: 'inline-block' }} />
        {t('wb.modules')}
      </div>
      <Tabs
        defaultActiveKey="modules"
        items={[
          {
            key: 'modules',
            label: t('wb.tabModules'),
            children: (
              <Row gutter={[14, 14]}>
                {MODULES.map((m) => (
                  <Col key={m.key} xs={24} sm={12} lg={8}>
                    <Card hoverable className="saas-card"
                      style={{ borderRadius: 14, height: '100%', border: '1px solid #EEF1F6' }}
                      onClick={() => navigate(m.key)}
                      styles={{ body: { padding: 18, display: 'flex', flexDirection: 'column', gap: 8, height: '100%' } }}>
                      <div style={{
                        width: 44, height: 44, borderRadius: 12, background: m.bg, color: m.color,
                        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
                      }}>{m.icon}</div>
                      <div style={{ fontSize: 15.5, fontWeight: 600 }}>{t(m.titleKey)}</div>
                      <div style={{ fontSize: 13, color: '#64748b', lineHeight: 1.65, flex: 1 }}>{t(m.descKey)}</div>
                      <div style={{ fontSize: 13, color: m.color, fontWeight: 500 }}>
                        {t('wb.enter')} <ArrowRightOutlined style={{ fontSize: 11 }} />
                      </div>
                    </Card>
                  </Col>
                ))}
                <Col xs={24} sm={12} lg={8}>
                  <Card hoverable className="saas-card"
                    style={{ borderRadius: 14, height: '100%', border: '1px dashed #D8DCE5' }}
                    onClick={() => navigate('/datasources')}
                    styles={{ body: { padding: 18, display: 'flex', flexDirection: 'column', gap: 8, height: '100%' } }}>
                    <div style={{
                      width: 44, height: 44, borderRadius: 12, background: '#f1f5f9', color: '#475569',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
                    }}><DatabaseOutlined /></div>
                    <div style={{ fontSize: 15.5, fontWeight: 600 }}>{t('wb.mConn')}</div>
                    <div style={{ fontSize: 13, color: '#64748b', lineHeight: 1.65, flex: 1 }}>
                      {t('wb.mConnDesc')}
                    </div>
                    <div style={{ fontSize: 13, color: '#475569', fontWeight: 500 }}>
                      {t('wb.manage')} <ArrowRightOutlined style={{ fontSize: 11 }} />
                    </div>
                  </Card>
                </Col>
              </Row>
            ),
          },
          {
            key: 'scenarios',
            label: t('wb.tabScenarios'),
            children: (
              <Row gutter={[14, 14]}>
                {SCENARIOS.map((sc) => (
                  <Col key={sc.titleKey} xs={24} sm={12} lg={8}>
                    <Card hoverable className="saas-card"
                      style={{ borderRadius: 14, height: '100%', border: '1px solid #EEF1F6' }}
                      onClick={() => navigate(sc.to)}
                      styles={{ body: { padding: 18, display: 'flex', flexDirection: 'column', gap: 10, height: '100%' } }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{
                          width: 38, height: 38, borderRadius: 10, background: sc.bg, color: sc.color,
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18,
                        }}>{sc.icon}</div>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>{t(sc.titleKey)}</div>
                      </div>
                      <div style={{ fontSize: 12.5, color: '#64748b', lineHeight: 1.7, flex: 1 }}>{t(sc.descKey)}</div>
                      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: '#475569', lineHeight: 1.9 }}>
                        {sc.points.map((p) => <li key={p}>{t(p)}</li>)}
                      </ul>
                    </Card>
                  </Col>
                ))}
              </Row>
            ),
          },
          {
            key: 'guide',
            label: t('wb.tabGuide'),
            children: (
              <Card style={{ borderRadius: 14, border: '1px solid #EEF1F6' }} styles={{ body: { padding: 20 } }}>
                <div style={{ display: 'flex', gap: 0, flexWrap: 'wrap' }}>
                  {GUIDE_STEPS.map((g, i) => (
                    <div key={g.titleKey} style={{ display: 'flex', alignItems: 'stretch', minWidth: 220, flex: 1 }}>
                      <div style={{ flex: 1, padding: '4px 14px' }}>
                        <div style={{
                          fontSize: 22, fontWeight: 700,
                          color: i === 0 ? '#16a34a' : '#cbd5e1',
                        }}>{String(i + 1).padStart(2, '0')}</div>
                        <div style={{ fontSize: 14, fontWeight: 600, margin: '4px 0 6px' }}>{t(g.titleKey)}</div>
                        <div style={{ fontSize: 12.5, color: '#64748b', lineHeight: 1.7 }}>{t(g.descKey)}</div>
                      </div>
                      {i < GUIDE_STEPS.length - 1 && (
                        <div style={{ display: 'flex', alignItems: 'center', color: '#d7dde8', fontSize: 18 }}>
                          <ArrowRightOutlined />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px dashed #e2e8f0', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <Button type="primary" icon={<MessageOutlined />} onClick={() => navigate('/chat')}>{t('wb.gAsk')}</Button>
                  <Button icon={<DatabaseOutlined />} onClick={() => navigate('/datasources')}>{t('wb.gConnect')}</Button>
                  <Button icon={<PieChartOutlined />} onClick={() => navigate('/explore')}>{t('wb.gChart')}</Button>
                </div>
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}
