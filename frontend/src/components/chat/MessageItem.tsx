/** 单条消息渲染：用户气泡 / assistant 结果卡 / 流式中的 assistant */

import { Avatar, Tag } from 'antd';
import { RobotOutlined, UserOutlined } from '@ant-design/icons';
import type { ChatMessage, StreamingState } from '../../api/types';
import {
  AnswerCard, ChartGallery, CodeCollapse, FollowupCapsules,
  MessageActions, TableTabs, TerminalCollapse,
} from './ResultBlocks';
import { StepProgress } from './StepProgress';

export interface MessageCallbacks {
  onAsk?: (q: string) => void;
  onPinChart?: (url: string, runId?: number) => void;
  onPinTable?: (name: string, rows: Record<string, unknown>[], runId?: number) => void;
  onPinText?: (text: string, runId?: number) => void;
  onCaptureSkill?: (runId: number) => void;
  onRerun?: (runId: number) => void;
}

export function UserBubble({ content }: { content: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '16px 0' }}>
      <div className="user-bubble">{content}</div>
      <Avatar style={{ marginLeft: 8, flexShrink: 0, background: '#94a3b8' }} icon={<UserOutlined />} />
    </div>
  );
}

function AssistantShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 10, margin: '16px 0' }}>
      <Avatar style={{ flexShrink: 0, background: 'linear-gradient(135deg,#2563eb,#7c3aed)' }} icon={<RobotOutlined />} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {children}
      </div>
    </div>
  );
}

/** 历史消息（来自后端，meta 齐全） */
export function MessageItem({ msg, cb }: { msg: ChatMessage; cb: MessageCallbacks }) {
  if (msg.role === 'user') {
    return <UserBubble content={msg.content} />;
  }
  const meta = msg.meta || {};
  const runId = meta.run_id;
  return (
    <AssistantShell>
      {meta.skill_replay && <Tag color="purple" style={{ marginBottom: 6 }}>Skill 复用</Tag>}
      {meta.ok === false && <Tag color="error" style={{ marginBottom: 6 }}>执行失败</Tag>}
      {msg.content
        ? <AnswerCard text={msg.content} />
        : <span style={{ color: '#94a3b8', fontSize: 13 }}>（本轮无文本结论）</span>}
      <ChartGallery
        charts={meta.charts || []}
        onPin={cb.onPinChart ? (u) => cb.onPinChart?.(u, runId) : undefined}
      />
      <TableTabs
        tables={meta.tables || {}}
        onPinTable={cb.onPinTable ? (n, rows) => cb.onPinTable?.(n, rows, runId) : undefined}
      />
      <CodeCollapse code={meta.code} plan={meta.plan} />
      <FollowupCapsules followups={meta.followups || []} onAsk={cb.onAsk} />
      <MessageActions
        msg={msg}
        onRerun={cb.onRerun && runId ? () => cb.onRerun?.(runId) : undefined}
        onCaptureSkill={cb.onCaptureSkill && runId && meta.ok ? () => cb.onCaptureSkill?.(runId) : undefined}
        onPinText={cb.onPinText && runId && meta.ok ? (text) => cb.onPinText?.(text, runId) : undefined}
      />
    </AssistantShell>
  );
}

/** 流式中的 assistant 消息：步骤进度 + 逐步抵达的结果块 */
export function StreamingItem({ st, cb }: { st: StreamingState; cb: MessageCallbacks }) {
  return (
    <AssistantShell>
      <StepProgress steps={st.steps} />
      {st.error ? (
        <div className="stream-error">{st.error}</div>
      ) : (
        <>
          {st.running && !st.answer && !st.charts.length && (
            <div className="thinking">
              <span className="dot" /><span className="dot" /><span className="dot" />
            </div>
          )}
          {st.answer && <AnswerCard text={st.answer} />}
          <ChartGallery
            charts={st.charts}
            onPin={cb.onPinChart ? (u) => cb.onPinChart?.(u) : undefined}
          />
          <TableTabs
            tables={st.tables}
            onPinTable={cb.onPinTable ? (n, rows) => cb.onPinTable?.(n, rows) : undefined}
          />
          <CodeCollapse code={st.code} plan={st.plan} />
          <TerminalCollapse stdout={st.stdout} stderr={st.stderr} />
          {!st.running && st.followups.length > 0 && (
            <FollowupCapsules followups={st.followups} onAsk={cb.onAsk} />
          )}
        </>
      )}
    </AssistantShell>
  );
}
