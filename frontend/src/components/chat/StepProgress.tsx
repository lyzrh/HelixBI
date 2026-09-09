import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons';
import type { StreamingState } from '../../api/types';

/** 竖向步骤进度条（复刻原 Streamlit step-row 视觉），Skill 运行弹窗复用 */
export function StepProgress({ steps }: { steps: StreamingState['steps'] }) {
  if (!steps.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, margin: '4px 0 8px' }}>
      {steps.map((s) => (
        <div key={s.node} style={{
          display: 'flex', alignItems: 'center', gap: 8, fontSize: 13,
          color: s.status === 'done' ? '#16a34a' : s.status === 'error' ? '#dc2626' : '#2563eb',
        }}>
          {s.status === 'running' && <LoadingOutlined spin />}
          {s.status === 'done' && <CheckCircleFilled />}
          {s.status === 'error' && <CloseCircleFilled />}
          <span>{s.label}</span>
          {s.detail && <span style={{ color: '#94a3b8', fontSize: 12 }}>{s.detail}</span>}
        </div>
      ))}
    </div>
  );
}
