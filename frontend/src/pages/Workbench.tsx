/** 工作台首页（FineDataLink demo中心风格）：欢迎横幅 + 统计卡 + Tab 化功能网格 */

import { useEffect } from 'react';
import { Button, Card, Col, Row, Tabs, Tag } from 'antd';
import {
  ArrowRightOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  MessageOutlined, PieChartOutlined, SoundOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const MODULES = [
  { key: '/chat', icon: <MessageOutlined />, title: '对话分析', desc: '自然语言提问，Agent 自动生成代码并沙箱执行，流式返回结论与图表', color: '#5645D4', bg: '#ECE7F8' },
  { key: '/explore', icon: <PieChartOutlined />, title: '自助分析', desc: '拖拽字段即时出图（柱状/折线/饼图），本地计算零消耗，可沉淀到仪表板', color: '#2A9D99', bg: '#D9F0EE' },
  { key: '/agents', icon: <ThunderboltOutlined />, title: '场景 Agent', desc: '零售销售 / 生产制造行业专家，预置口径与推荐问题，开箱即聊', color: '#7B5CF5', bg: '#EFE9FC' },
  { key: '/skills', icon: <ExperimentOutlined />, title: 'Skill 库', desc: '验证过的分析路径自动沉淀，相似问题秒级重放，持续积累分析资产', color: '#B7791F', bg: '#FBF3DC' },
  { key: '/insights', icon: <SoundOutlined />, title: '主动洞察', desc: '规则引擎扫描指标突变与异常，LLM 生成经营诊断，不错过关键信号', color: '#DD5B00', bg: '#FFE9D6' },
  { key: '/dashboards', icon: <DashboardOutlined />, title: '仪表板', desc: '图表 / 表格 / 结论 / 洞察统一沉淀，支持导出 HTML 报告', color: '#C2255C', bg: '#FDE0EC' },
];

const SCENARIOS = [
  {
    title: '零售经营监控', icon: <PieChartOutlined />, to: '/agents',
    color: '#2A9D99', bg: '#f0fdfa',
    desc: '面向零售销售域：品类 / 区域 / 门店 / 渠道多口径分析，客单价等派生指标自动对齐口径。',
    points: ['各品类销售额 TopN 与占比', '近 90 天销售趋势与环比', '区域客单价对比'],
  },
  {
    title: '生产质量分析', icon: <ThunderboltOutlined />, to: '/agents',
    color: '#5645D4', bg: '#ECE7F8',
    desc: '面向生产制造域：产量 / 良率 / 达成率 / 停机时长，OEE 近似口径预置。',
    points: ['各产线产量与达成率', '良率趋势与异常预警', '停机时长排行'],
  },
  {
    title: '异常主动预警', icon: <SoundOutlined />, to: '/insights',
    color: '#ea580c', bg: '#fff7ed',
    desc: '定时扫描全部数据源，指标突变 / 连续下滑 / TopN 变动自动发现并生成经营诊断。',
    points: ['环比突变检测', '新告警自动 LLM 诊断', '一键固定到仪表板'],
  },
];

const GUIDE_STEPS = [
  { title: '连接数据', desc: '上传 CSV / Excel / Parquet 文件，或配置数据库连接并物化为缓存' },
  { title: '对话提问', desc: '用自然语言描述分析诉求，Agent 生成代码并在沙箱执行，流式返回图表与结论' },
  { title: '拖拽探索', desc: '在自助分析页点击或拖拽字段，零 token 秒级出图，随时切换图表类型' },
  { title: '沉淀资产', desc: '把有价值的图表固定到仪表板、把验证过的分析沉淀为 Skill，越用越聪明' },
];

export function Workbench() {
  const navigate = useNavigate();
  const stats = useAppStore((s) => s.stats);
  const loadStats = useAppStore((s) => s.loadStats);
  const health = useAppStore((s) => s.health);

  useEffect(() => { loadStats(); }, [loadStats]);

  const statCards = [
    { label: '数据源', value: stats?.data_sources ?? 0, hint: '可分析的数据连接' },
    { label: '分析会话', value: stats?.sessions ?? 0, hint: '累计对话会话数' },
    {
      label: '运行成功率', value: stats?.runs ? `${Math.round(((stats.runs_ok ?? 0) / stats.runs) * 100)}%` : '-',
      hint: `${stats?.runs ?? 0} 次沙箱执行`,
    },
    { label: 'Skill 沉淀', value: stats?.skills ?? 0, hint: '可复用分析路径' },
    { label: '新洞察', value: stats?.insights_new ?? 0, hint: '待处理的经营信号' },
    { label: '仪表板', value: stats?.dashboards ?? 0, hint: '已固定的分析资产' },
    { label: '总输入 Token', value: formatNumber(stats?.token_total_input ?? 0), hint: 'LLM 上下文消耗' },
    { label: '总输出 Token', value: formatNumber(stats?.token_total_output ?? 0), hint: '代码与结论生成量' },
    { label: 'API 总花费', value: `¥${(stats?.token_total_cost ?? 0).toFixed(4)}`, hint: `累计 ${stats?.token_runs ?? 0} 次调用` },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1240, margin: '0 auto' }}>
      {/* 欢迎横幅：深海军蓝 + 紫色主 CTA（绎紫设计系统） */}
      <div style={{
        borderRadius: 12, padding: '32px 36px', color: '#fff', position: 'relative', overflow: 'hidden',
        background: '#0A1530',
      }}>
        <div style={{
          position: 'absolute', right: -40, top: -60, width: 260, height: 260, borderRadius: '50%',
          background: 'rgba(86,69,212,0.18)',
        }} />
        <div style={{
          position: 'absolute', right: 90, bottom: -80, width: 190, height: 190, borderRadius: '50%',
          background: 'rgba(255,255,255,0.04)',
        }} />
        <div style={{ position: 'relative' }}>
          <div style={{ fontSize: 13, color: '#D6B6F6', fontWeight: 500 }}>面向制造业与零售业的对话式智能分析</div>
          <div style={{ fontSize: 26, fontWeight: 600, margin: '6px 0 10px', letterSpacing: -0.5 }}>
            欢迎使用 绎数 · 企业级数据分析助手
          </div>
          <div style={{ fontSize: 13.5, color: '#A4A097', maxWidth: 620, lineHeight: 1.7 }}>
            用自然语言提问，Agent 自动完成数据理解、代码生成与沙箱执行；
            拖拽字段即时出图；分析资产沉淀为 Skill 与仪表板，越用越聪明。
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 18 }}>
            <Button type="primary" size="large" onClick={() => navigate('/chat')}
              style={{ fontWeight: 500 }}>
              开始对话分析 <ArrowRightOutlined />
            </Button>
            <Button size="large" ghost onClick={() => navigate('/explore')}
              style={{ color: '#fff', borderColor: '#A4A097' }}>
              自助拖拽分析
            </Button>
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
            <Tag style={{ background: '#1A2A52', color: '#A4A097', border: 'none', borderRadius: 999 }}>
              模型：{health?.llm_configured ? health.model : '未配置'}
            </Tag>
            <Tag style={{ background: '#1A2A52', color: '#A4A097', border: 'none', borderRadius: 999 }}>
              沙箱：{health?.sandbox ? '在线' : '离线'}
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
        功能模块
      </div>
      <Tabs
        defaultActiveKey="modules"
        items={[
          {
            key: 'modules',
            label: '功能模块',
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
                      <div style={{ fontSize: 15.5, fontWeight: 600 }}>{m.title}</div>
                      <div style={{ fontSize: 13, color: '#64748b', lineHeight: 1.65, flex: 1 }}>{m.desc}</div>
                      <div style={{ fontSize: 13, color: m.color, fontWeight: 500 }}>
                        进入 <ArrowRightOutlined style={{ fontSize: 11 }} />
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
                    <div style={{ fontSize: 15.5, fontWeight: 600 }}>数据连接</div>
                    <div style={{ fontSize: 13, color: '#64748b', lineHeight: 1.65, flex: 1 }}>
                      上传 CSV / Excel / Parquet，或连接 MySQL / PostgreSQL / SQLite（物化后分析）
                    </div>
                    <div style={{ fontSize: 13, color: '#475569', fontWeight: 500 }}>
                      管理 <ArrowRightOutlined style={{ fontSize: 11 }} />
                    </div>
                  </Card>
                </Col>
              </Row>
            ),
          },
          {
            key: 'scenarios',
            label: '价值场景',
            children: (
              <Row gutter={[14, 14]}>
                {SCENARIOS.map((sc) => (
                  <Col key={sc.title} xs={24} sm={12} lg={8}>
                    <Card hoverable style={{ borderRadius: 12, height: '100%' }}
                      onClick={() => navigate(sc.to)}
                      styles={{ body: { padding: 18, display: 'flex', flexDirection: 'column', gap: 10, height: '100%' } }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{
                          width: 38, height: 38, borderRadius: 10, background: sc.bg, color: sc.color,
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18,
                        }}>{sc.icon}</div>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>{sc.title}</div>
                      </div>
                      <div style={{ fontSize: 12.5, color: '#64748b', lineHeight: 1.7, flex: 1 }}>{sc.desc}</div>
                      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: '#475569', lineHeight: 1.9 }}>
                        {sc.points.map((p) => <li key={p}>{p}</li>)}
                      </ul>
                    </Card>
                  </Col>
                ))}
              </Row>
            ),
          },
          {
            key: 'guide',
            label: '使用指南',
            children: (
              <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 20 } }}>
                <div style={{ display: 'flex', gap: 0, flexWrap: 'wrap' }}>
                  {GUIDE_STEPS.map((g, i) => (
                    <div key={g.title} style={{ display: 'flex', alignItems: 'stretch', minWidth: 220, flex: 1 }}>
                      <div style={{ flex: 1, padding: '4px 14px' }}>
                        <div style={{
                          fontSize: 22, fontWeight: 700,
                          color: i === 0 ? '#16a34a' : '#cbd5e1',
                        }}>{String(i + 1).padStart(2, '0')}</div>
                        <div style={{ fontSize: 14, fontWeight: 600, margin: '4px 0 6px' }}>{g.title}</div>
                        <div style={{ fontSize: 12.5, color: '#64748b', lineHeight: 1.7 }}>{g.desc}</div>
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
                  <Button type="primary" icon={<MessageOutlined />} onClick={() => navigate('/chat')}>去提问</Button>
                  <Button icon={<DatabaseOutlined />} onClick={() => navigate('/datasources')}>先接数据</Button>
                  <Button icon={<PieChartOutlined />} onClick={() => navigate('/explore')}>拖拽出图</Button>
                </div>
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}
