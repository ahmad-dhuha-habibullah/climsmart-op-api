import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

export interface Series { name: string; data: [string, number | null][]; type?: "line" | "bar"; axis?: 0 | 1; color?: string; dashed?: boolean }

/** Time-series chart; two y axes so APSIM state (left) can be overlaid with weather (right). */
export function TimeChart({ series, yNames, height = 300 }: { series: Series[]; yNames: [string, string?]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current);
    c.setOption({
      textStyle: { fontFamily: "Plus Jakarta Sans" },
      tooltip: { trigger: "axis" }, legend: { top: 0 },
      grid: { left: 50, right: yNames[1] ? 50 : 16, top: 30, bottom: 50 },
      xAxis: { type: "time" },
      yAxis: [{ type: "value", name: yNames[0] }, ...(yNames[1] ? [{ type: "value", name: yNames[1], splitLine: { show: false } }] : [])],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
      series: series.map((s) => ({
        name: s.name, type: s.type ?? "line", data: s.data, yAxisIndex: s.axis ?? 0, showSymbol: false, connectNulls: false,
        itemStyle: s.color ? { color: s.color } : undefined, lineStyle: { width: 1.5, type: s.dashed ? "dashed" : "solid" },
      })),
    });
    const ro = new ResizeObserver(() => c.resize()); ro.observe(el.current);
    return () => { ro.disconnect(); c.dispose(); };
  }, [series, yNames]);
  return <div ref={el} style={{ height, width: "100%" }} />;
}
