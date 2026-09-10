/** 轻量国际化：中/英界面词典 + 语言状态（localStorage 持久化，切换即时生效） */

import { create } from 'zustand';

export type Lang = 'zh' | 'en';

const LANG_KEY = 'helix-lang';

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === 'zh' || saved === 'en') return saved;
  } catch { /* ignore */ }
  return 'zh';
}

interface LangState {
  lang: Lang;
  setLang: (l: Lang) => void;
}

export const useLangStore = create<LangState>((set) => ({
  lang: initialLang(),
  setLang: (l) => {
    try { localStorage.setItem(LANG_KEY, l); } catch { /* ignore */ }
    set({ lang: l });
  },
}));

const STR = {
  zh: {
    // 导航
    'nav.workbench': '工作台',
    'nav.chat': '对话分析',
    'nav.explore': '自助分析',
    'nav.agents': '场景 Agent',
    'nav.skills': 'Skill 库',
    'nav.insights': '主动洞察',
    'nav.dashboards': '仪表板',
    'nav.datasources': '数据源',
    // 侧边栏状态
    'status.sandbox': '沙箱',
    'status.online': '在线',
    'status.offline': '离线',
    'status.model': '模型',
    'status.notConfigured': '未配置',
    'status.tipSettings': '设置中心（LLM 接口 / 偏好设置 / 个人资料）',
    // 工作台 Hero
    'wb.eyebrow': '面向制造业与零售业的对话式智能分析',
    'wb.title': '欢迎使用 绎数 · 企业级数据分析助手',
    'wb.body': '用自然语言提问，Agent 自动完成数据理解、代码生成与沙箱执行；拖拽字段即时出图；分析资产沉淀为 Skill 与仪表板，越用越聪明。',
    'wb.btnChat': '开始对话分析',
    'wb.btnExplore': '自助拖拽分析',
    'wb.chipModel': '模型',
    'wb.chipSandbox': '沙箱',
    // 工作台统计卡
    'wb.statDs': '数据源', 'wb.statDsHint': '可分析的数据连接',
    'wb.statSessions': '分析会话', 'wb.statSessionsHint': '累计对话会话数',
    'wb.statSuccess': '运行成功率', 'wb.statSuccessHint': '近 5 次沙箱执行',
    'wb.statSkills': 'Skill 沉淀', 'wb.statSkillsHint': '可复用分析路径',
    'wb.statInsights': '新洞察', 'wb.statInsightsHint': '待处理的经营信号',
    'wb.statDash': '仪表板', 'wb.statDashHint': '已固定的分析资产',
    'wb.statTokenIn': '总输入 Token', 'wb.statTokenInHint': 'LLM 上下文消耗',
    'wb.statTokenOut': '总输出 Token', 'wb.statTokenOutHint': '代码与结论生成量',
    'wb.statCost': 'API 总花费', 'wb.statCostHint': '累计 {n} 次调用',
    'wb.runsUnit': '次沙箱执行',
    // 工作台模块
    'wb.modules': '功能模块',
    'wb.tabModules': '功能模块', 'wb.tabScenarios': '价值场景', 'wb.tabGuide': '使用指南',
    'wb.mChat': '对话分析',
    'wb.mChatDesc': '自然语言提问，Agent 自动生成代码并沙箱执行，流式返回结论与图表',
    'wb.mExplore': '自助分析',
    'wb.mExploreDesc': '拖拽字段即时出图（柱状/折线/饼图），本地计算零消耗，可沉淀到仪表板',
    'wb.mAgents': '场景 Agent',
    'wb.mAgentsDesc': '零售销售 / 生产制造行业专家，预置口径与推荐问题，开箱即聊',
    'wb.mSkills': 'Skill 库',
    'wb.mSkillsDesc': '验证过的分析路径自动沉淀，相似问题秒级重放，持续积累分析资产',
    'wb.mInsights': '主动洞察',
    'wb.mInsightsDesc': '规则引擎扫描指标突变与异常，LLM 生成经营诊断，不错过关键信号',
    'wb.mDash': '仪表板',
    'wb.mDashDesc': '图表 / 表格 / 结论 / 洞察统一沉淀，支持导出 HTML 报告',
    'wb.mConn': '数据连接',
    'wb.mConnDesc': '上传 CSV / Excel / Parquet，或连接 MySQL / PostgreSQL / SQLite（物化后分析）',
    'wb.enter': '进入', 'wb.manage': '管理',
    // 价值场景
    'wb.sc1': '零售经营监控',
    'wb.sc1Desc': '面向零售销售域：品类 / 区域 / 门店 / 渠道多口径分析，客单价等派生指标自动对齐口径。',
    'wb.sc1p1': '各品类销售额 TopN 与占比', 'wb.sc1p2': '近 90 天销售趋势与环比', 'wb.sc1p3': '区域客单价对比',
    'wb.sc2': '生产质量分析',
    'wb.sc2Desc': '面向生产制造域：产量 / 良率 / 达成率 / 停机时长，OEE 近似口径预置。',
    'wb.sc2p1': '各产线产量与达成率', 'wb.sc2p2': '良率趋势与异常预警', 'wb.sc2p3': '停机时长排行',
    'wb.sc3': '异常主动预警',
    'wb.sc3Desc': '定时扫描全部数据源，指标突变 / 连续下滑 / TopN 变动自动发现并生成经营诊断。',
    'wb.sc3p1': '环比突变检测', 'wb.sc3p2': '新告警自动 LLM 诊断', 'wb.sc3p3': '一键固定到仪表板',
    // 使用指南
    'wb.g1': '连接数据', 'wb.g1Desc': '上传 CSV / Excel / Parquet 文件，或配置数据库连接并物化为缓存',
    'wb.g2': '对话提问', 'wb.g2Desc': '用自然语言描述分析诉求，Agent 生成代码并在沙箱执行，流式返回图表与结论',
    'wb.g3': '拖拽探索', 'wb.g3Desc': '在自助分析页点击或拖拽字段，零 token 秒级出图，随时切换图表类型',
    'wb.g4': '沉淀资产', 'wb.g4Desc': '把有价值的图表固定到仪表板、把验证过的分析沉淀为 Skill，越用越聪明',
    'wb.gAsk': '去提问', 'wb.gConnect': '先接数据', 'wb.gChart': '拖拽出图',
    // 设置中心
    'set.title': '设置中心',
    'set.tabLlm': 'LLM 接口', 'set.tabPrefs': '偏好设置', 'set.tabProfile': '个人资料',
    'set.lang': '界面语言',
    'set.langHint': '切换后界面文案立即生效（当前覆盖导航 / 工作台 / 设置中心，更多页面翻译见 Roadmap）',
    'set.langZh': '中文', 'set.langEn': 'English',
    // LLM tab
    'set.provider': '服务商（点击快捷填充）', 'set.providerCustom': '自定义',
    'set.baseUrl': '接口地址（Base URL）',
    'set.apiKey': 'API Key', 'set.apiKeyKeep': '当前：{m}（留空则沿用）', 'set.apiKeyKeepShort': '留空保持 {m} 不变',
    'set.modelName': '模型名',
    'set.test': '测试连接', 'set.save': '保存并生效',
    'set.needKey': '请先填写 API Key', 'set.needUrl': '请填写接口地址', 'set.needModel': '请填写模型名',
    'set.llmNote': '保存后立即生效（无需重启），同时写入 .env 持久化。测试连接只发送一次极小请求（max_tokens=1），几乎不消耗额度。',
    // 偏好 tab
    'set.answerStyle': '回答风格', 'set.styleConcise': '简洁', 'set.styleStandard': '标准', 'set.styleDetailed': '详细',
    'set.styleHint': '简洁：100 字内只给结论与关键数字；标准：默认平衡；详细：结论 + 依据 + 数据细节 + 业务解读',
    'set.creativity': '回答创意度', 'set.cPrecise': '精确', 'set.cBalanced': '平衡', 'set.cCreative': '创意',
    'set.creativityHint': '精确：代码与数字更稳定（推荐分析场景）；创意：表达更多样，适合探索性讨论',
    'set.followups': '追问推荐',
    'set.followupsOn': '每次回答后推荐 3 个后续问题', 'set.followupsOff': '关闭后不再推荐（每轮可省一次模型调用）',
    'set.customInstr': '自定义指令（助手人设）',
    'set.savePrefs': '保存偏好', 'set.prefsSaved': '偏好已保存，下一轮分析生效',
    // 资料 tab
    'set.nickname': '昵称', 'set.nicknamePh': '你的名字', 'set.unnamed': '未命名',
    'set.role': '职位 / 角色（可从下拉选择，也可自由输入）',
    'set.rolePh': '选择或输入，如：数据分析师 / 运营经理',
    'set.avatarColor': '头像颜色',
    'set.saveProfile': '保存资料', 'set.profileSaved': '个人资料已保存', 'set.needNickname': '昵称不能为空',
  },
  en: {
    'nav.workbench': 'Workbench',
    'nav.chat': 'Chat Analysis',
    'nav.explore': 'Self-serve Analytics',
    'nav.agents': 'Scenario Agents',
    'nav.skills': 'Skill Library',
    'nav.insights': 'Proactive Insights',
    'nav.dashboards': 'Dashboards',
    'nav.datasources': 'Data Sources',
    'status.sandbox': 'Sandbox',
    'status.online': 'Online',
    'status.offline': 'Offline',
    'status.model': 'Model',
    'status.notConfigured': 'Not configured',
    'status.tipSettings': 'Settings (LLM / Preferences / Profile)',
    'wb.eyebrow': 'Conversational analytics for manufacturing & retail',
    'wb.title': 'Welcome to Helix BI · Enterprise Data Analysis Assistant',
    'wb.body': 'Ask in natural language — the Agent understands your data, generates code and runs it in a secure sandbox. Drag fields to chart instantly; analyses are captured as Skills and Dashboards — the system gets smarter with every use.',
    'wb.btnChat': 'Start Chat Analysis',
    'wb.btnExplore': 'Drag & Drop Analytics',
    'wb.chipModel': 'Model',
    'wb.chipSandbox': 'Sandbox',
    'wb.statDs': 'Data Sources', 'wb.statDsHint': 'Ready-to-analyze connections',
    'wb.statSessions': 'Chat Sessions', 'wb.statSessionsHint': 'Sessions in total',
    'wb.statSuccess': 'Run Success Rate', 'wb.statSuccessHint': 'Recent sandbox runs',
    'wb.statSkills': 'Skills Captured', 'wb.statSkillsHint': 'Reusable analysis paths',
    'wb.statInsights': 'New Insights', 'wb.statInsightsHint': 'Signals awaiting review',
    'wb.statDash': 'Dashboards', 'wb.statDashHint': 'Pinned analysis assets',
    'wb.statTokenIn': 'Total Input Tokens', 'wb.statTokenInHint': 'LLM context usage',
    'wb.statTokenOut': 'Total Output Tokens', 'wb.statTokenOutHint': 'Code & conclusion generation',
    'wb.statCost': 'Total API Cost', 'wb.statCostHint': '{n} API calls in total',
    'wb.runsUnit': 'sandbox runs',
    'wb.modules': 'Modules',
    'wb.tabModules': 'Modules', 'wb.tabScenarios': 'Scenarios', 'wb.tabGuide': 'Guide',
    'wb.mChat': 'Chat Analysis',
    'wb.mChatDesc': 'Ask in natural language — the Agent generates code, runs it in a sandbox and streams back charts and conclusions',
    'wb.mExplore': 'Self-serve Analytics',
    'wb.mExploreDesc': 'Drag fields to chart instantly (bar / line / pie), fully local with zero tokens, pinnable to dashboards',
    'wb.mAgents': 'Scenario Agents',
    'wb.mAgentsDesc': 'Retail & manufacturing experts with built-in metric semantics and recommended questions, ready to chat',
    'wb.mSkills': 'Skill Library',
    'wb.mSkillsDesc': 'Validated analysis paths are captured automatically and replayed in seconds for similar questions',
    'wb.mInsights': 'Proactive Insights',
    'wb.mInsightsDesc': 'Rule engine scans for metric shifts and anomalies; LLM generates business diagnosis for key signals',
    'wb.mDash': 'Dashboards',
    'wb.mDashDesc': 'Charts / tables / conclusions / insights in one place, exportable as self-contained HTML reports',
    'wb.mConn': 'Data Connections',
    'wb.mConnDesc': 'Upload CSV / Excel / Parquet, or connect MySQL / PostgreSQL / SQLite (materialized for analysis)',
    'wb.enter': 'Open', 'wb.manage': 'Manage',
    'wb.sc1': 'Retail Monitoring',
    'wb.sc1Desc': 'For retail sales: category / region / store / channel analysis with derived metrics like basket size aligned automatically.',
    'wb.sc1p1': 'TopN categories by sales & share', 'wb.sc1p2': '90-day sales trend & MoM', 'wb.sc1p3': 'Basket size by region',
    'wb.sc2': 'Production Quality',
    'wb.sc2Desc': 'For manufacturing: output / yield rate / attainment / downtime with OEE-approximate semantics built in.',
    'wb.sc2p1': 'Output & attainment by line', 'wb.sc2p2': 'Yield trend & anomaly alerts', 'wb.sc2p3': 'Downtime ranking',
    'wb.sc3': 'Proactive Alerts',
    'wb.sc3Desc': 'Scans all data sources on schedule: shifts, sustained declines and TopN changes, with LLM diagnosis.',
    'wb.sc3p1': 'MoM shift detection', 'wb.sc3p2': 'Auto LLM diagnosis for new alerts', 'wb.sc3p3': 'One-click pin to dashboard',
    'wb.g1': 'Connect Data', 'wb.g1Desc': 'Upload CSV / Excel / Parquet files, or configure database connections (materialized to cache)',
    'wb.g2': 'Ask Questions', 'wb.g2Desc': 'Describe your analysis in natural language; the Agent generates code, runs it and streams back charts',
    'wb.g3': 'Explore by Drag', 'wb.g3Desc': 'Click or drag fields to chart in seconds with zero tokens; switch chart types anytime',
    'wb.g4': 'Capture Assets', 'wb.g4Desc': 'Pin valuable charts to dashboards, capture validated analyses as Skills — smarter with use',
    'wb.gAsk': 'Ask Now', 'wb.gConnect': 'Connect Data', 'wb.gChart': 'Chart by Drag',
    'set.title': 'Settings',
    'set.tabLlm': 'LLM Config', 'set.tabPrefs': 'Preferences', 'set.tabProfile': 'Profile',
    'set.lang': 'Interface Language',
    'set.langHint': 'Applies instantly to UI text (covers navigation / workbench / settings for now — more pages on the Roadmap)',
    'set.langZh': '中文', 'set.langEn': 'English',
    'set.provider': 'Providers (click to fill)', 'set.providerCustom': 'Custom',
    'set.baseUrl': 'Base URL',
    'set.apiKey': 'API Key', 'set.apiKeyKeep': 'Current: {m} (leave blank to keep)', 'set.apiKeyKeepShort': 'Leave blank to keep {m}',
    'set.modelName': 'Model Name',
    'set.test': 'Test Connection', 'set.save': 'Save & Apply',
    'set.needKey': 'Enter your API Key first', 'set.needUrl': 'Base URL is required', 'set.needModel': 'Model name is required',
    'set.llmNote': 'Takes effect immediately after saving (no restart) and is persisted to .env. Connection tests send one tiny request (max_tokens=1) and cost almost nothing.',
    'set.answerStyle': 'Answer Style', 'set.styleConcise': 'Concise', 'set.styleStandard': 'Standard', 'set.styleDetailed': 'Detailed',
    'set.styleHint': 'Concise: conclusions & key numbers within 100 words; Standard: balanced by default; Detailed: conclusion + evidence + data details + business reading',
    'set.creativity': 'Creativity', 'set.cPrecise': 'Precise', 'set.cBalanced': 'Balanced', 'set.cCreative': 'Creative',
    'set.creativityHint': 'Precise: steadier code & numbers (recommended for analysis); Creative: richer wording for exploratory discussion',
    'set.followups': 'Follow-up Suggestions',
    'set.followupsOn': 'Suggest 3 follow-up questions after each answer', 'set.followupsOff': 'Disabled — saves one LLM call per turn',
    'set.customInstr': 'Custom Instructions (assistant persona)',
    'set.savePrefs': 'Save Preferences', 'set.prefsSaved': 'Preferences saved — applies from the next analysis',
    'set.nickname': 'Nickname', 'set.nicknamePh': 'Your name', 'set.unnamed': 'Unnamed',
    'set.role': 'Role (pick from the list or type your own)',
    'set.rolePh': 'Choose or type, e.g. Data Analyst / Operations Manager',
    'set.avatarColor': 'Avatar Color',
    'set.saveProfile': 'Save Profile', 'set.profileSaved': 'Profile saved', 'set.needNickname': 'Nickname cannot be empty',
  },
} as const;

export type StrKey = keyof typeof STR.zh;

/** 翻译函数：useT() 返回 t(key, params?)，{n}/{m} 等占位符按 params 替换 */
export function useT() {
  const lang = useLangStore((s) => s.lang);
  return (key: StrKey, params?: Record<string, string | number>): string => {
    let text: string = STR[lang][key] ?? STR.zh[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        text = text.replaceAll(`{${k}}`, String(v));
      }
    }
    return text;
  };
}
