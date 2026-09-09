/** 操作流程面板（参考 FineDataLink：01-05 步横向流程 + 可折叠 + 当前步高亮） */

import { useState, type ReactNode } from 'react';
import { RightOutlined } from '@ant-design/icons';

export interface FlowStep {
  title: string;
  desc: string;
  /** 自定义操作区（如跳转按钮） */
  action?: ReactNode;
}

interface Props {
  steps: FlowStep[];
  /** 完成到第几步（1-based，0 表示全部未完成） */
  current?: number;
  defaultOpen?: boolean;
}

export function OperationFlow({ steps, current = 0, defaultOpen = true }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <span
          className="flow-toggle"
          style={{ fontSize: 13, color: '#2563eb', cursor: 'pointer', userSelect: 'none' }}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '收起操作流程' : '展开操作流程'}
        </span>
      </div>
      {open && (
        <div
          style={{
            position: 'relative',
            background: 'linear-gradient(90deg,#eff6ff 0%,#f0f9ff 100%)',
            border: '1px solid #dbeafe',
            borderRadius: 12,
            padding: '18px 20px 16px',
            display: 'flex',
            gap: 0,
            flexWrap: 'wrap',
          }}
        >
          {steps.map((s, i) => {
            const done = current > i + 1;
            const active = current === i + 1;
            const color = done ? '#16a34a' : active ? '#2563eb' : '#94a3b8';
            const bg = done ? '#f0fdf4' : active ? '#eff6ff' : '#f8fafc';
            return (
              <div key={s.title} style={{ display: 'flex', alignItems: 'stretch', minWidth: 190, flex: 1 }}>
                <div style={{ flex: 1, padding: '4px 12px' }}>
                  <div style={{ fontSize: 24, fontWeight: 800, color, lineHeight: 1.1 }}>
                    {String(i + 1).padStart(2, '0')}
                  </div>
                  <div style={{ fontSize: 13.5, fontWeight: 600, margin: '6px 0 6px', color: done || active ? '#1e293b' : '#64748b' }}>
                    {s.title}
                  </div>
                  <div style={{ fontSize: 12, color: '#64748b', lineHeight: 1.7 }}>{s.desc}</div>
                  {s.action && (
                    <div style={{ marginTop: 8, background: bg, borderRadius: 8, padding: '5px 10px', display: 'inline-block' }}>
                      {s.action}
                    </div>
                  )}
                </div>
                {i < steps.length - 1 && (
                  <div style={{ display: 'flex', alignItems: 'center', color: '#bfdbfe', fontSize: 16 }}>
                    <RightOutlined />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
