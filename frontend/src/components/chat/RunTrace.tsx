/** 单次运行的阶段时间线（可观测性面板）。
 *
 * 数据来自 `GET /api/runs/{id}` 的 `trace` 字段（后端 Run.trace）：
 * 分阶段耗时 / LLM 调用与 token / Skill 命中模式 / 校验结论。
 * 其中「Skill 重放 → LLM calls == 0」是从数据里读出来的事实，不是文案。
 */

import { useEffect, useState } from 'react';
import { Collapse, Spin, Tag, Timeline } from 'antd';
import { FieldTimeOutlined } from '@ant-design/icons';
import { api } from '../../api/client';
import type { RunTrace } from '../../api/types';

/** 意图来源（后端 intent_source）：这一步到底调没调 LLM */
const INTENT_SOURCE: Record<string, { color: string; text: string }> = {
  deterministic: { color: 'green', text: '意图解析：确定性（0 次 LLM）' },
  llm: { color: 'blue', text: '意图解析：调用 LLM' },
  predefined: { color: 'default', text: '意图解析：已确认的 QuerySpec' },
  budget_fallback: { color: 'orange', text: '意图解析：预算拦下，退回确定性' },
};

/** 终止原因（后端 cost_control.termination_reason） */
const TERMINATION_TAG: Record<string, string> = {
  llm_call_limit: 'LLM 调用次数达预算上限',
  input_token_limit: '输入 Token 达预算上限',
  output_token_limit: '输出 Token 达预算上限',
  total_token_limit: '总 Token 达预算上限',
  cost_limit: '估算成本达预算上限',
  environment_unavailable: '沙箱环境不可用',
};

function fmtMs(ms?: number | null): string {
  if (ms === null || ms === undefined) return '-';
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

const VALIDATION_TAG: Record<string, { color: string; text: string }> = {
  ok: { color: 'success', text: '校验通过' },
  warn: { color: 'warning', text: '校验告警' },
  fail: { color: 'error', text: '校验失败' },
};

const SKILL_TAG: Record<string, { color: string; text: string }> = {
  replay: { color: 'purple', text: 'Skill 重放（0 次 LLM 调用）' },
  few_shot: { color: 'geekblue', text: 'Skill few-shot 注入' },
  fresh: { color: 'default', text: '全新生成' },
};

/** 自修复结局（后端 self_repair.outcome） */
const REPAIR_OUTCOME: Record<string, { color: string; text: string }> = {
  success: { color: 'success', text: '成功' },
  exhausted: { color: 'error', text: '修复耗尽' },
  fallback: { color: 'warning', text: '兜底失败' },
  runtime_error: { color: 'error', text: '链路异常' },
};

function StatItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ minWidth: 96 }}>
      <div style={{ fontSize: 11.5, color: '#94a3b8' }}>{label}</div>
      <div style={{ fontSize: 14.5, fontWeight: 600, color: '#1e293b' }}>{value}</div>
    </div>
  );
}

function TraceBody({ trace }: { trace: RunTrace }) {
  const validation = VALIDATION_TAG[trace.validation?.status] || VALIDATION_TAG.warn;
  const skill = SKILL_TAG[trace.skill?.mode] || SKILL_TAG.fresh;
  const totalTokens = (trace.llm?.input_tokens || 0) + (trace.llm?.output_tokens || 0);
  const repair = trace.self_repair;
  const repairOutcome = REPAIR_OUTCOME[repair?.outcome || ''] ;
  const cost = trace.cost_control;
  const perf = trace.performance;
  const budgetLimit = cost?.budget_limit;
  const budgetUsed = cost?.budget_used?.llm_calls;
  // 只有显式配了上限才展示"预算"，否则满屏的 x/undefined 反而是噪声
  const budgetCap = budgetLimit?.max_llm_calls ?? undefined;
  const intentTag = cost?.intent_source ? INTENT_SOURCE[cost.intent_source] : undefined;
  const terminationText = cost?.termination_reason
    ? (TERMINATION_TAG[cost.termination_reason] || cost.termination_label || cost.termination_reason)
    : '';
  const genContext = perf?.context?.generation;
  const contextBlocks = genContext?.blocks || [];

  return (
    <div>
      {/* 汇总指标条 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, margin: '4px 0 12px' }}>
        <StatItem label="总耗时" value={fmtMs(trace.latency_ms)} />
        <StatItem label="LLM 调用" value={trace.llm?.calls ?? 0} />
        {totalTokens > 0 && <StatItem label="Tokens" value={totalTokens.toLocaleString()} />}
        <StatItem label="代码执行" value={`${trace.execution?.ok ? '成功' : '失败'} / ${trace.execution?.attempts ?? 1} 次`} />
        {(trace.execution?.repair_count ?? 0) > 0
          && <StatItem label="自修复" value={`${trace.execution.repair_count} 次`} />}
        <StatItem
          label="结果验收"
          value={<Tag color={validation.color} style={{ margin: 0 }}>{validation.text}</Tag>}
        />
      </div>

      {/* Self-Repair V2：为什么修 / 修了几次 / 每轮耗时 / 怎么收场 */}
      {repair && repair.policy !== 'not_applicable' && !repair.first_pass_success && (
        <div style={{
          border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 10px',
          marginBottom: 12, background: '#f8fafc',
        }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
            {repairOutcome && <Tag color={repairOutcome.color} style={{ margin: 0 }}>{repairOutcome.text}</Tag>}
            <Tag style={{ margin: 0 }}>{repair.error_label || repair.error_category || '未分类'}</Tag>
            {repair.repair_strategy && <Tag color="geekblue" style={{ margin: 0 }}>策略 {repair.repair_strategy}</Tag>}
            {repair.repeated_error && <Tag color="volcano" style={{ margin: 0 }}>复读提前终止</Tag>}
            <Tag style={{ margin: 0 }}>
              修复 {repair.repair_attempts}/{repair.max_fix_attempts} 次
            </Tag>
            {(repair.repair_latency_ms?.total_ms ?? 0) > 0 && (
              <Tag style={{ margin: 0 }}>修复耗时 {fmtMs(repair.repair_latency_ms?.total_ms)}</Tag>
            )}
          </div>
          {(repair.attempts || []).length > 0 && (
            <div style={{ marginTop: 6 }}>
              {(repair.attempts || []).map((a, i) => (
                <div key={i} style={{ fontSize: 12, color: '#64748b', display: 'flex', gap: 8 }}>
                  <span>#{a.attempt ?? i + 1}</span>
                  <span>{a.trigger_category}</span>
                  <span>→ {a.strategy}</span>
                  <span>{fmtMs(a.duration_ms)}</span>
                  <span>{a.ok ? '已修复' : `仍为 ${a.result_category || '失败'}`}</span>
                </div>
              ))}
            </div>
          )}
          {repair.repair_reason && (
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>{repair.repair_reason}</div>
          )}
        </div>
      )}

      {/* 成本控制：调用次数归因 / 预算 / 终止原因 / 缓存与瓶颈 */}
      {cost && (
        <div style={{
          border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 10px',
          marginBottom: 12, background: '#f8fafc',
        }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
            <Tag color="purple" style={{ margin: 0 }}>
              LLM 调用 {cost.llm_calls ?? 0} 次
            </Tag>
            {(cost.total_tokens ?? 0) > 0 && (
              <Tag style={{ margin: 0 }}>
                Token {cost.input_tokens}+{cost.output_tokens}
              </Tag>
            )}
            {intentTag && <Tag color={intentTag.color} style={{ margin: 0 }}>{intentTag.text}</Tag>}
            {cost.followup_source === 'deterministic' && (
              <Tag color="green" style={{ margin: 0 }}>追问推荐：确定性（0 次 LLM）</Tag>
            )}
            {cost.zero_llm && <Tag color="purple" style={{ margin: 0 }}>Skill 重放：零 LLM</Tag>}
            {terminationText && <Tag color="orange" style={{ margin: 0 }}>{terminationText}</Tag>}
          </div>

          {/* 预算使用率：能看出"离上限还有多远" */}
          {budgetCap && budgetUsed !== undefined && (
            <div style={{ fontSize: 12, color: '#64748b', marginTop: 6 }}>
              预算：LLM 调用 {budgetUsed}/{budgetCap}
              {budgetLimit?.max_total_tokens
                ? `　Token ${cost.budget_used?.total_tokens ?? 0}/${budgetLimit.max_total_tokens}`
                : ''}
              {cost.usage_source === 'estimated' ? '　（用量来自本地估算）' : ''}
            </div>
          )}

          {/* 性能：瓶颈 + 缓存命中 */}
          {perf && (
            <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>
              {perf.bottleneck?.node
                ? `最慢阶段：${perf.bottleneck.label || perf.bottleneck.node} ${fmtMs(perf.bottleneck.duration_ms)}`
                : ''}
              {perf.cache && (perf.cache.hits > 0 || perf.cache.misses > 0)
                ? `　缓存命中 ${perf.cache.hits}/${perf.cache.hits + perf.cache.misses}（${Math.round((perf.cache.hit_rate || 0) * 100)}%）`
                : ''}
            </div>
          )}

          {/* 生成 prompt 的分块记账：Token 花在哪一块 */}
          {contextBlocks.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
              <span style={{ fontSize: 12, color: '#94a3b8', alignSelf: 'center' }}>
                生成上下文 {genContext?.total_tokens ?? 0} tok：
              </span>
              {contextBlocks.map((b) => (
                <Tag key={b.name} style={{ fontSize: 11.5, margin: 0 }}>
                  {b.name} {b.tokens}
                </Tag>
              ))}
            </div>
          )}

          {(cost.budget_reason || cost.termination_label) && (
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
              {cost.termination_label || cost.budget_reason}
            </div>
          )}
        </div>
      )}

      {/* Skill 命中模式 */}
      {trace.skill?.hit && (
        <div style={{ marginBottom: 12 }}>
          <Tag color={skill.color}>{skill.text}</Tag>
          {trace.semantic?.resolved_metrics?.length ? (
            <Tag>解析口径：{trace.semantic.resolved_metrics.join(' / ')}</Tag>
          ) : null}
        </div>
      )}

      {/* 阶段时间线 */}
      <Timeline
        items={(trace.stages || []).map((s, i) => ({
          key: `${s.node}-${i}`,
          color: s.status === 'error' ? 'red' : '#5645D4',
          children: (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ fontSize: 12.5, color: '#334155' }}>{s.label || s.node}</span>
              <span style={{ fontSize: 12, color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                {fmtMs(s.duration_ms)}
              </span>
            </div>
          ),
        }))}
      />

      {/* LLM 调用分布 */}
      {trace.llm?.calls > 0 && trace.llm.by_node
        && Object.keys(trace.llm.by_node).length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
          {Object.entries(trace.llm.by_node).map(([node, calls]) => (
            <Tag key={node} style={{ fontSize: 11.5 }}>{node} × {calls}</Tag>
          ))}
        </div>
      )}
    </div>
  );
}

/** 挂在历史 assistant 消息下：懒加载该轮的运行轨迹 */
export function RunTraceCollapse({ runId }: { runId?: number }) {
  const [trace, setTrace] = useState<RunTrace | null | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    if (!runId) return undefined;
    api.get<{ trace?: RunTrace | null }>(`/api/runs/${runId}`)
      .then((info) => { if (alive) setTrace(info.trace || null); })
      .catch(() => { if (alive) setTrace(null); });
    return () => { alive = false; };
  }, [runId]);

  if (!runId) return null;

  return (
    <Collapse
      size="small"
      ghost
      style={{ marginTop: 8 }}
      items={[{
        key: 'trace',
        label: <span style={{ fontSize: 12.5, color: '#64748b' }}><FieldTimeOutlined /> 运行时间线</span>,
        children: trace === undefined
          ? <div style={{ textAlign: 'center', padding: 12 }}><Spin size="small" /></div>
          : trace === null
            ? <span style={{ fontSize: 12.5, color: '#94a3b8' }}>本轮没有轨迹数据（早于可观测功能的历史运行）</span>
            : <TraceBody trace={trace} />,
      }]}
    />
  );
}
