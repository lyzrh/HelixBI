/** assistant 结果块组合：结论 markdown / 图表 / 表格 / 代码 / 终端 / 追问胶囊 */

import { App as AntApp, Button, Collapse, Space, Tabs, Tooltip } from 'antd';
import { CodeOutlined, DownloadOutlined, FileExcelOutlined, PushpinOutlined, StarOutlined, SyncOutlined } from '@ant-design/icons';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../../api/client';
import type { ChatMessage } from '../../api/types';

export function AnswerCard({ text }: { text: string }) {
  return (
    <div className="answer-md">
      <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
    </div>
  );
}

export function ChartGallery({ charts, onPin }: { charts: string[]; onPin?: (url: string) => void }) {
  if (!charts.length) return null;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12, margin: '10px 0' }}>
      {charts.map((url) => (
        <div key={url} style={{ position: 'relative' }}>
          <img src={url} alt="图表" style={{ width: '100%', borderRadius: 8, border: '1px solid #e2e8f0', background: '#fff' }} />
          {onPin && (
            <Tooltip title="固定到仪表板">
              <Button size="small" icon={<PushpinOutlined />} style={{ position: 'absolute', top: 8, right: 8 }}
                onClick={() => onPin(url)} />
            </Tooltip>
          )}
        </div>
      ))}
    </div>
  );
}

/** 结果表导出为 Excel（多行数据完整导出，不受前端截断影响） */
export async function exportRowsAsExcel(filename: string, sheetName: string, rows: Record<string, unknown>[]) {
  try {
    const resp = await fetch('/api/utils/export_table', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ filename, sheets: [{ name: sheetName, rows }] }),
    });
    if (!resp.ok) throw new Error(`导出失败 (${resp.status})`);
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${filename || '导出数据'}.xlsx`;
    a.click();
    URL.revokeObjectURL(a.href);
    return true;
  } catch {
    return false;
  }
}

export function TableTabs({ tables, onPinTable }: { tables: Record<string, Record<string, unknown>[]>; onPinTable?: (name: string, rows: Record<string, unknown>[]) => void }) {
  const { message } = AntApp.useApp();
  const entries = Object.entries(tables || {}).filter(([, rows]) => rows?.length);
  if (!entries.length) return null;
  return (
    <Tabs size="small" style={{ margin: '8px 0' }} items={entries.map(([name, rows]) => ({
      key: name,
      label: <span>
        {name}
        {onPinTable && (
          <Button type="text" size="small" icon={<PushpinOutlined />} style={{ marginLeft: 6 }}
            onClick={() => onPinTable(name, rows)} />
        )}
      </span>,
      children: (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
            <Button size="small" icon={<FileExcelOutlined />} onClick={async () => {
              const ok = await exportRowsAsExcel(name, name, rows);
              if (ok) message.success(`已导出「${name}」（${rows.length} 行）`);
              else message.error('导出失败');
            }}>导出 Excel</Button>
          </div>
          <ResultTable rows={rows} />
        </div>
      ),
    }))} />
  );
}

export function ResultTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows?.length) return null;
  const headers = Object.keys(rows[0]);
  return (
    <div className="thin-scroll" style={{ overflow: 'auto', maxHeight: 360, border: '1px solid #e2e8f0', borderRadius: 8 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12.5 }}>
        <thead>
          <tr>{headers.map((h) => <th key={h} style={{ position: 'sticky', top: 0, background: '#f8fafc', border: '1px solid #e2e8f0', padding: '6px 10px', textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {headers.map((h) => (
                <td key={h} style={{ border: '1px solid #eef2f7', padding: '5px 10px', whiteSpace: 'nowrap' }}>
                  {r[h] === null || r[h] === undefined ? '-' : String(r[h])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CodeCollapse({ code, plan }: { code?: string; plan?: string }) {
  if (!code) return null;
  return (
    <Collapse size="small" ghost items={[{
      key: 'code',
      label: <span style={{ fontSize: 12.5, color: '#64748b' }}><CodeOutlined /> 分析代码（含计划）</span>,
      children: (
        <>
          {plan && <div style={{ fontSize: 12.5, color: '#475569', marginBottom: 8, whiteSpace: 'pre-wrap' }}>{plan}</div>}
          <pre style={{ background: '#0f172a', color: '#e2e8f0', padding: 12, borderRadius: 8, fontSize: 12, overflow: 'auto', maxHeight: 320 }}>
            <code>{code}</code>
          </pre>
        </>
      ),
    }]} />
  );
}

export function TerminalCollapse({ stdout, stderr }: { stdout?: string; stderr?: string }) {
  if (!stdout && !stderr) return null;
  return (
    <Collapse size="small" ghost items={[{
      key: 'term',
      label: <span style={{ fontSize: 12.5, color: '#64748b' }}>执行日志</span>,
      children: <div className="terminal">
        {stdout && <div>{stdout}</div>}
        {stderr && <div className="err">{stderr}</div>}
      </div>,
    }]} />
  );
}

export function FollowupCapsules({ followups, onAsk }: { followups: string[]; onAsk?: (q: string) => void }) {
  if (!followups?.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
      {followups.map((q) => (
        <span key={q} className="followup-pill" onClick={() => onAsk?.(q)}>{q}</span>
      ))}
    </div>
  );
}

/** assistant 消息操作条：重跑 / 导出 / 沉淀 Skill / 固定结论 */
export function MessageActions({ msg, onRerun, onCaptureSkill, onPinText }: {
  msg: ChatMessage;
  onRerun?: () => void;
  onCaptureSkill?: (runId: number) => void;
  onPinText?: (answer: string) => void;
}) {
  const runId = msg.meta?.run_id;
  if (!runId) return null;
  return (
    <Space size={4} style={{ marginTop: 8 }}>
      {onRerun && <Button size="small" icon={<SyncOutlined />} onClick={onRerun}>重跑</Button>}
      <Button size="small" icon={<DownloadOutlined />}
        onClick={() => window.open(`/api/runs/${runId}/export`, '_blank')}>导出报告</Button>
      {onCaptureSkill && (
        <Button size="small" icon={<StarOutlined />} onClick={() => onCaptureSkill(runId)}>沉淀为 Skill</Button>
      )}
      {onPinText && msg.meta?.ok && (
        <Button size="small" icon={<PushpinOutlined />} onClick={() => onPinText(msg.content)}>固定结论</Button>
      )}
    </Space>
  );
}
