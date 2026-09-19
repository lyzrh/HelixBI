/** 受保护图片：/runs 等鉴权文件下发路径无法用 <img> 直接携带 Authorization，
 * 统一通过 fetch(blob) 渲染；401/404 显示占位。 */

import { useEffect, useState } from 'react';
import { authHeaders } from '../../api/client';

export function AuthImg({ src, alt, style }: {
  src: string; alt?: string; style?: React.CSSProperties;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    setBlobUrl(null);
    setFailed(false);
    fetch(src, { headers: authHeaders() })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.blob();
      })
      .then((b) => {
        if (cancelled) return;
        revoked = URL.createObjectURL(b);
        setBlobUrl(revoked);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [src]);

  if (failed) {
    return (
      <div style={{
        ...style, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#98A2B3', fontSize: 12, minHeight: 80, background: '#f8fafc',
        borderRadius: 8, border: '1px dashed #e2e8f0',
      }}>
        图片不可用
      </div>
    );
  }
  if (!blobUrl) {
    return (
      <div style={{
        ...style, minHeight: 80, background: '#f8fafc', borderRadius: 8,
        border: '1px dashed #e2e8f0',
      }} />
    );
  }
  return <img src={blobUrl} alt={alt || "图表"} style={style} />;
}
