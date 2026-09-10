/** 绎数品牌图形：双螺旋 Helix 标识（紫色=业务数据，青色=分析智能，黄色节点=沉淀资产） */

const PURPLE = '#8B77E8';
const TEAL = '#4FC3BC';
const YELLOW = '#F5D75E';
const WHITE = '#FFFFFF';

export function HelixMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M15 5C34 11 34 24 15 30C4 34 3 40 12 44"
        stroke={PURPLE} strokeWidth="3.4" strokeLinecap="round"
      />
      <path
        d="M33 43C14 37 14 24 33 18C44 14 45 8 36 4"
        stroke={TEAL} strokeWidth="3.4" strokeLinecap="round"
      />
      <circle cx="15" cy="5" r="2.6" fill={YELLOW} />
      <circle cx="36" cy="4" r="2.6" fill={WHITE} />
      <circle cx="33" cy="43" r="2.6" fill={YELLOW} />
      <circle cx="12" cy="44" r="2.6" fill={WHITE} />
    </svg>
  );
}

/** 侧边栏 / 深色场景下的应用图标底座 */
export function HelixBadge({ size = 34, radius = 9 }: { size?: number; radius?: number }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: radius, background: '#1A2A52',
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
    }}>
      <HelixMark size={Math.round(size * 0.6)} />
    </div>
  );
}
