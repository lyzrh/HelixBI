/** 工作台首页（FineDataLink demo中心风格）：欢迎横幅 + 统计卡 + Tab 化功能网格 */

import { useEffect } from 'react';
import { Button, Card, Col, Row, Tabs, Tag } from 'antd';
import {
  ArrowRightOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  MessageOutlined, PieChartOutlined, SoundOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';
import { useT, type StrKey } from '../i18n';

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const MODULES = [
  { key: '/chat', icon: <MessageOutlined />, titleKey: 'wb.mChat' as StrKey, descKey: 'wb.mChatDesc' as StrKey, color: '#5645D4', bg: '#ECE7F8' },
  { key: '/explore', icon: <PieChartOutlined />, titleKey: 'wb.mExplore' as StrKey, descKey: 'wb.mExploreDesc' as StrKey, color: '#2A9D99', bg: '#D9F0EE' },
  { key: '/agents', icon: <ThunderboltOutlined />, titleKey: 'wb.mAgents' as StrKey, descKey: 'wb.mAgentsDesc' as StrKey, color: '#7B5CF5', bg: '#EFE9FC' },
  { key: '/skills', icon: <ExperimentOutlined />, titleKey: 'wb.mSkills' as StrKey, descKey: 'wb.mSkillsDesc' as StrKey, color: '#B7791F', bg: '#FBF3DC' },
  { key: '/insights', icon: <SoundOutlined />, titleKey: 'wb.mInsights' as StrKey, descKey: 'wb.mInsightsDesc' as StrKey, color: '#DD5B00', bg: '#FFE9D6' },
  { key: '/dashboards', icon: <DashboardOutlined />, titleKey: 'wb.mDash' as StrKey, descKey: 'wb.mDashDesc' as StrKey, color: '#C2255C', bg: '#FDE0EC' },
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
    color: '#5645D4', bg: '#ECE7F8',
    descKey: 'wb.sc2Desc' as StrKey,
    points: ['wb.sc2p1', 'wb.sc2p2', 'wb.sc2p3'] as StrKey[],
  },
  {
    titleKey: 'wb.sc3' as StrKey, icon: <SoundOutlined />, to: '/insights',
    color: '#DD5B00', bg: '#FFE9D6',
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

export function Workbench() {
  const navigate = useNavigate();
  const t = useT();
  const stats = useAppStore((s) => s.stats);
  const loadStats = useAppStore((s) => s.loadStats);
  const health = useAppStore((s) => s.health);

  useEffect(() => { loadStats(); }, [loadStats]);

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
    <div style={{ padding: 24, maxWidth: 1240, margin: '0 auto' }}>
      {/* 欢迎横幅：品牌渐变（Premium SaaS 主视觉，全站唯一大面积渐变） */}
      <div className="brand-gradient" style={{
        borderRadius: 16, padding: '34px 38px', color: '#fff', position: 'relative', overflow: 'hidden',
        boxShadow: '0 10px 30px rgba(47,27,102,0.28)',
      }}>
        <div style={{
          position: 'absolute', right: -60, top: -80, width: 320, height: 320, borderRadius: '50%',
          background: 'rgba(255,255,255,0.07)',
        }} />
        <div style={{
          position: 'absolute', right: 120, bottom: -110, width: 240, height: 240, borderRadius: '50%',
          background: 'rgba(255,255,255,0.05)',
        }} />
        <div style={{ position: 'relative' }}>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.72)', fontWeight: 600, letterSpacing: 1.2 }}>
            TURN DATA INTO DECISIONS.
          </div>
          <div style={{ fontSize: 27, fontWeight: 700, margin: '8px 0 10px', letterSpacing: -0.5 }}>
            {t('wb.title')}
          </div>
          <div style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.78)', maxWidth: 620, lineHeight: 1.7 }}>
            {t('wb.body')}
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
            <Button size="large" onClick={() => navigate('/chat')}
              style={{ fontWeight: 600, border: 'none', color: '#4534B3', background: '#fff',
                       boxShadow: '0 2px 8px rgba(10,21,48,0.18)' }}>
              {t('wb.btnChat')} <ArrowRightOutlined />
            </Button>
            <Button size="large" ghost onClick={() => navigate('/explore')}
              style={{ color: '#fff', borderColor: 'rgba(255,255,255,0.55)' }}>
              {t('wb.btnExplore')}
            </Button>
          </div>
          <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
            <Tag style={{ background: 'rgba(255,255,255,0.12)', color: '#fff', border: 'none', borderRadius: 999 }}>
              {t('wb.chipModel')}：{health?.llm_configured ? health.model : t('status.notConfigured')}
            </Tag>
            <Tag style={{ background: 'rgba(255,255,255,0.12)', color: '#fff', border: 'none', borderRadius: 999 }}>
              {t('wb.chipSandbox')}：{health?.sandbox ? t('status.online') : t('status.offline')}
            </Tag>
          </div>
        </div>
      </div>

      {/* 统计卡 */}
      <Row gutter={[14, 14]} style={{ marginTop: 18 }}>
        {statCards.map((s) => (
          <Col key={s.label} xs={12} sm={8} lg={6}>
            <Card size="small" style={{ borderRadius: 12 }} styles={{ body: { padding: '14px 16px' } }}>
              <div style={{ fontSize: 12.5, color: '#64748b' }}>{s.label}</div>
              <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4, color: '#0f172a' }}>{s.value}</div>
              <div style={{ fontSize: 11.5, color: '#94a3b8', marginTop: 2 }}>{s.hint}</div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 功能模块（Tab 化：模块 / 价值场景 / 使用指南） */}
      <div style={{ fontSize: 15, fontWeight: 600, margin: '24px 0 4px', display: 'flex', alignItems: 'center', gap: 8 }}>
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
                    <Card hoverable style={{ borderRadius: 12, height: '100%' }}
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
                  <Card hoverable style={{ borderRadius: 12, height: '100%', borderStyle: 'dashed' }}
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
                    <Card hoverable style={{ borderRadius: 12, height: '100%' }}
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
              <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 20 } }}>
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
