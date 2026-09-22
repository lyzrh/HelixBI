/** 后端 REST/SSE 接口的 TypeScript 类型定义 */

export interface SemanticPack {
  id: string;
  name: string;
}

export interface HealthInfo {
  status: string;
  sandbox: boolean;
  model: string;
  llm_configured: boolean;
  packs: SemanticPack[];
}

export interface Stats {
  sessions: number;
  runs: number;
  runs_ok: number;
  data_sources: number;
  skills: number;
  agents: number;
  insights_new: number;
  dashboards: number;
  token_total_input: number;
  token_total_output: number;
  token_total_cost: number;
  token_runs: number;
}

export interface QuerySpec {
  metrics?: string[];
  dimensions?: string[];
  filters?: { field: string; op: string; value: unknown }[];
  time_range?: { type?: string; n?: number; start?: string; end?: string };
  grain?: string;
  compare?: string | null;
  topn?: number | null;
  chart?: string;
  rewritten_question?: string;
}

export interface ChatMessage {
  id: number;
  session_id: number;
  role: 'user' | 'assistant';
  content: string;
  meta: {
    run_id?: number;
    charts?: string[];
    tables?: Record<string, Record<string, unknown>[]>;
    followups?: string[];
    spec?: QuerySpec;
    attempts?: number;
    ok?: boolean;
    code?: string;
    plan?: string;
    skill_replay?: boolean;
  };
  created_at: string;
}

export interface SceneAgentInfo {
  id: number;
  name: string;
  description: string;
  pack_id: string;
  data_source_ids: number[];
  intro: string;
  recommended_questions: string[];
  icon: string;
  color: string;
  enabled: boolean;
  builtin: boolean;
}

export interface SessionInfo {
  id: number;
  title: string;
  agent_id: number | null;
  created_at: string;
  updated_at: string;
  message_count?: number;
  agent?: SceneAgentInfo;
  messages?: ChatMessage[];
}

export interface DataSourceInfo {
  id: number;
  name: string;
  type: 'file' | 'db';
  file_path: string | null;
  size_bytes: number | null;
  db_type: string | null;
  host: string | null;
  port: number | null;
  database_name: string | null;
  username: string | null;
  pack_id: string | null;
  columns: string[];
  row_count: number | null;
  materialized_path: string | null;
  materialized_table: string | null;
  materialized_at: string | null;
  materialized_truncated: boolean;
  builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkillInfo {
  id: number;
  name: string;
  description: string;
  pack_id: string | null;
  question: string;
  spec: QuerySpec;
  code: string;
  columns: string[];
  source_run_id: number | null;
  tags: string[];
  use_count: number;
  success_count: number;
  enabled: boolean;
  builtin: boolean;
  created_at: string;
}

export interface InsightInfo {
  id: number;
  data_source_id: number;
  data_source_name?: string;
  rule_id: string;
  severity: 'info' | 'warning' | 'critical';
  title: string;
  detail: string;
  evidence: Record<string, unknown>;
  report: string;
  status: 'new' | 'read' | 'resolved';
  created_at: string;
}

export interface DashboardItemInfo {
  id: number;
  dashboard_id: number;
  type: 'chart' | 'table' | 'text' | 'insight';
  title: string;
  payload: {
    chart_url?: string;
    chart_option?: Record<string, unknown>;
    chart_kind?: string;
    table_name?: string;
    rows?: Record<string, unknown>[];
    text?: string;
    insight_id?: number;
    // 自助分析保存的实时组件：可刷新
    data_source_id?: number;
    query_spec?: {
      dimensions: string[];
      metrics: { field: string; agg: string }[];
      filters?: { field: string; op: string; value: unknown }[];
      date_grain?: string | null;
      sort_field?: string | null;
      sort_order?: string;
      limit?: number;
    };
  };
  source_run_id: number | null;
  sort_order: number;
  span?: number;
  created_at: string;
}

export interface DashboardInfo {
  id: number;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  item_count?: number;
  items?: DashboardItemInfo[];
}

/** 自助分析字段探测 */
export interface ExploreField {
  name: string;
  dtype: 'text' | 'number' | 'date';
  sample: string;
}

/** 字段画像（数据概览） */
export interface FieldProfile {
  name: string;
  dtype: 'text' | 'number' | 'date';
  count: number;
  missing_rate: number;
  distinct: number;
  // number
  min?: number; max?: number; mean?: number; median?: number; std?: number;
  p25?: number; p75?: number; zeros?: number; negatives?: number;
  hist?: { bins: string[]; counts: number[] } | null;
  // date
  span_days?: number;
  // text
  top_values?: { value: string; count: number }[];
}

export interface ProfileInfo {
  rows: number;
  duplicate_rate: number;
  fields: FieldProfile[];
}

/** 工作台最近分析 */
export interface RunRecent {
  id: number;
  session_id: number;
  question: string;
  status: string;
  ok: boolean;
  attempts: number;
  duration_ms: number | null;
  created_at: string;
}

/** 定时洞察扫描配置 */
export interface InsightScheduleInfo {
  enabled: boolean;
  auto_diagnose: boolean;
  interval_minutes: number;
  last_run_at: string;
  next_run_at: string;
  last_result: {
    ran_at?: string;
    scanned?: number;
    total?: number;
    new?: number;
    diagnosed?: number;
  };
}

export interface RunInfo {
  id: number;
  session_id: number;
  question: string;
  status: string;
  ok: boolean;
  code: string;
  attempts: number;
  charts: string[];
  tables: Record<string, Record<string, unknown>[]>;
  answer: string;
  followups: string[];
  duration_ms: number | null;
  created_at: string;
  /** 可观测轨迹（早于该功能的历史运行没有此字段） */
  trace?: RunTrace | null;
}

/** 单次运行的可观测轨迹（backend/analysis/runtime.py `_build_trace` / skills 重放共用） */
export interface RunTrace {
  run_id: number;
  question: string;
  model: string;
  latency_ms: number;
  stages_ms?: number;
  stages: { node: string; label: string; duration_ms?: number; status?: string }[];
  semantic?: {
    packs?: string[];
    source?: string;
    resolved_metrics?: string[];
    resolved_dimensions?: string[];
    analysis_type?: string;
    resolver_confidence?: number;
  };
  skill: { matched_ids: number[]; hit: boolean; mode: string };
  execution: { ok: boolean; attempts: number; repair_count: number; sandboxed: boolean };
  /** Self-Repair V2 观测段：为什么修 / 修了几次 / 每轮耗时 / 最终如何收场 */
  self_repair?: {
    policy: string;
    first_pass_success: boolean;
    outcome: string;
    repair_status: string;
    repair_attempts: number;
    max_fix_attempts: number;
    executions: number;
    error_category: string;
    error_label?: string;
    error_signature?: string;
    repair_strategy?: string;
    repair_reason?: string;
    repeated_error?: boolean;
    repeat_kind?: string;
    error_chain?: string[];
    attempts?: {
      attempt?: number;
      trigger_category?: string;
      strategy?: string;
      focus?: string;
      duration_ms?: number;
      ok?: boolean;
      result_category?: string;
    }[];
    repair_latency_ms?: { count: number; p50_ms: number; p95_ms: number; max_ms: number; total_ms: number };
  };

  llm: {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    by_node?: Record<string, number>;
  };
  validation: {
    status: 'ok' | 'warn' | 'fail';
    checks: { name: string; ok: boolean; detail: string }[];
    failed: string[];
  };
  final_status: string;
}

/** SSE 事件 */
export type SseEvent =
  | { event: 'step'; data: { node: string; label: string; status: 'running' | 'done' | 'error'; detail?: string; attempt?: number } }
  | { event: 'spec'; data: { spec: QuerySpec } }
  | { event: 'code'; data: { plan: string; code: string; attempt: number } }
  | { event: 'execute'; data: { ok: boolean; stdout: string; stderr: string; charts: string[]; tables: Record<string, Record<string, unknown>[]>; text: string } }
  | { event: 'answer'; data: { answer: string } }
  | { event: 'followups'; data: { followups: string[] } }
  | { event: 'charts'; data: { charts: string[] } }
  | { event: 'tables'; data: { tables: Record<string, Record<string, unknown>[]> } }
  | { event: 'done'; data: { run_id: number; message_id: number; ok: boolean; duration_ms: number } }
  | { event: 'error'; data: { message: string } };

/** 流式中的 assistant 消息（前端渲染状态） */
export interface StreamingState {
  running: boolean;
  steps: { node: string; label: string; status: 'running' | 'done' | 'error'; detail?: string }[];
  spec?: QuerySpec;
  plan?: string;
  code?: string;
  attempts: number;
  stdout?: string;
  stderr?: string;
  charts: string[];
  tables: Record<string, Record<string, unknown>[]>;
  answer?: string;
  followups: string[];
  error?: string;
}
