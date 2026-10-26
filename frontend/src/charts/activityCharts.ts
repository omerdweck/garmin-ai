/**
 * Charts of one workout. The x axis is distance when the workout has it
 * (runs, rides), time otherwise; every chart of a workout shares the same
 * axis and the same ECharts group, so touching one moves the pointer on all
 * of them - and on the map.
 */

import { base, type Palette } from "./Chart";
import type { ChartOption } from "./echarts";

export type Points = {
  t: (number | null)[]; d: (number | null)[]; hr: (number | null)[]; speed: (number | null)[];
  elev: (number | null)[]; cad: (number | null)[]; power: (number | null)[];
  lat: (number | null)[]; lon: (number | null)[];
};

export const CHART_GROUP = "activity";

export function xAxisOf(points: Points): { values: number[]; label: (v: number) => string; byDistance: boolean } {
  const byDistance = points.d.length > 0 && (points.d.at(-1) ?? 0) > 500;
  const values = (byDistance ? points.d : points.t).map((v) => v ?? 0);
  return {
    values,
    byDistance,
    // Kilometres, or minutes into the workout.
    label: byDistance ? (v) => (v / 1000).toFixed(v < 10000 ? 1 : 0) : (v) => `${Math.round(v / 60)}′`,
  };
}

/** Seconds per km from metres per second; standing still is a gap, not a 40-minute km. */
export const paceOf = (speed: number | null) => (speed && speed > 0.8 ? 1000 / speed : null);

export type Series = { values: (number | null)[]; color: string; inverse?: boolean; format: (v: number) => string; area?: boolean };

export function metricChart(p: Palette, points: Points, series: Series): ChartOption {
  const x = xAxisOf(points);
  const present = series.values.filter((v): v is number => v !== null);
  const min = present.length ? Math.min(...present) : 0;
  const max = present.length ? Math.max(...present) : 1;
  const pad = (max - min) * 0.08 || 1;
  return {
    ...base(p),
    grid: { left: 40, right: 10, top: 10, bottom: 22 },
    xAxis: {
      type: "category",
      data: x.values,
      boundaryGap: false,
      axisLine: { lineStyle: { color: p.line } },
      axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 10, formatter: (v: string) => x.label(Number(v)), hideOverlap: true, interval: Math.max(1, Math.floor(x.values.length / 5)) },
    },
    yAxis: {
      type: "value",
      inverse: series.inverse,
      min: Math.floor(min - pad),
      max: Math.ceil(max + pad),
      splitNumber: 3,
      axisLabel: { color: p.muted, fontSize: 10, formatter: (v: number) => series.format(v) },
      splitLine: { lineStyle: { color: p.line, type: "dashed" } },
    },
    tooltip: {
      ...(base(p).tooltip as object),
      formatter: (params: unknown) => {
        const item = (params as { dataIndex: number; value: number | null }[])[0];
        const at = x.label(x.values[item.dataIndex]) + (x.byDistance ? ' ק"מ' : "");
        return item.value == null ? at : `${at}<br/><b>${series.format(item.value)}</b>`;
      },
    },
    series: [{
      type: "line",
      data: series.values,
      showSymbol: false,
      smooth: 0.3,
      connectNulls: false,
      lineStyle: { width: 1.8, color: series.color },
      itemStyle: { color: series.color },
      areaStyle: series.area === false ? undefined : {
        origin: series.inverse ? "end" : "start",
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: series.inverse
            ? [{ offset: 0, color: `${series.color}05` }, { offset: 1, color: `${series.color}40` }]
            : [{ offset: 0, color: `${series.color}40` }, { offset: 1, color: `${series.color}05` }],
        },
      },
    }],
  };
}

export type Length = { index: number; seconds: number | null; meters: number | null; stroke: string | null; strokes: number | null; swolf: number | null };

export const STROKE_LABEL: Record<string, string> = {
  freestyle: "חתירה", backstroke: "גב", breaststroke: "חזה", butterfly: "פרפר", drill: "תרגיל", mixed: "מעורב", im: "מעורב",
};
const STROKE_COLOR: Record<string, string> = {
  freestyle: "#1ba39c", backstroke: "#3b6fe0", breaststroke: "#e0a21b", butterfly: "#e8572a", drill: "#9aa0a6", mixed: "#7b5cd6", im: "#7b5cd6",
};

/** Pool lengths: a bar per length (time, coloured by stroke) and the SWOLF line over them. */
export function lengthsChart(p: Palette, lengths: Length[]): ChartOption {
  return {
    ...base(p),
    grid: { left: 32, right: 32, top: 14, bottom: 22 },
    xAxis: { type: "category", data: lengths.map((l) => l.index), axisTick: { show: false }, axisLine: { lineStyle: { color: p.line } }, axisLabel: { color: p.muted, fontSize: 10, hideOverlap: true } },
    yAxis: [
      { type: "value", axisLabel: { color: p.muted, fontSize: 10, formatter: (v: number) => `${v}″` }, splitLine: { lineStyle: { color: p.line, type: "dashed" } } },
      { type: "value", scale: true, splitLine: { show: false }, axisLabel: { color: p.muted, fontSize: 10 } },
    ],
    tooltip: { ...(base(p).tooltip as object), axisPointer: { type: "shadow" } },
    series: [
      {
        name: "זמן", type: "bar", barMaxWidth: 12,
        data: lengths.map((l) => ({ value: l.seconds, itemStyle: { color: STROKE_COLOR[l.stroke ?? ""] ?? p.accent, borderRadius: [3, 3, 0, 0] } })),
      },
      { name: "SWOLF", type: "line", yAxisIndex: 1, showSymbol: false, smooth: true, lineStyle: { width: 2, color: p.text }, itemStyle: { color: p.text }, data: lengths.map((l) => l.swolf) },
    ],
  };
}
