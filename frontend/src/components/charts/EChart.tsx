/** ECharts 通用封装：初始化 / setOption / 自适应尺寸 */

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

export function EChart({ option, height = 380 }: { option: echarts.EChartsOption; height?: number }) {
  const divRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!divRef.current) return;
    chartRef.current = echarts.init(divRef.current);
    const onResize = () => chartRef.current?.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={divRef} style={{ width: '100%', height }} />;
}
