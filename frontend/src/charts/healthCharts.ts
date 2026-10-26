/**
 * Charts for the health and goals tabs. Like workoutCharts.ts: data and
 * palette in, ECharts option out.
 */

import { base, type Palette } from "./Chart";
import type { ChartOption } from "./echarts";

export type Point = [string, number];
export type Band = { mean: number; low: number; high: number } | null;
export type Span = { start: string; end: string };
export type SleepNight = {
  date: string; deep: number; light: number; rem: number; awake: number;
  score: number | null; bed: string | null; wake: string | null;
};

const parse = (iso: string) => new Date(`${iso}T00:00:00`);
const dayMonth = (iso: string) => {
  const d = parse(iso);
  return `${d.getDate()}.${d.getMonth() + 1}`;
};

const axisX = (p: Palette, dates: string[]) => ({
  type: "category" as const,
  data: dates,
  boundaryGap: false,
  axisLine: { lineStyle: { color: p.line } },
  axisTick: { show: false },
  axisLabel: { color: p.muted, fontSize: 10, formatter: dayMonth, hideOverlap: true },
});

const axisY = (p: Palette, extra: object = {}) => ({
  type: "value" as const,
  scale: true,
  axisLabel: { color: p.muted, fontSize: 10 },
  splitLine: { lineStyle: { color: p.line, type: "dashed" as const } },
  ...extra,
});

/** Every calendar day in [start, end], so gaps show as gaps rather than being squeezed out. */
function allDays(start: string, end: string): string[] {
  const out: string[] = [];
  for (let d = parse(start); d <= parse(end); d.setDate(d.getDate() + 1)) {
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
  }
  return out;
}

function movingAverage(values: (number | null)[], window: number): (number | null)[] {
  return values.map((_, i) => {
    const slice = values.slice(Math.max(0, i - window + 1), i + 1).filter((v): v is number => v !== null);
    return slice.length >= Math.ceil(window / 2) ? +(slice.reduce((a, b) => a + b, 0) / slice.length).toFixed(1) : null;
  });
}

/**
 * One health metric over the period: the daily line, the personal normal as
 * a shaded band, and the days the coach raised an alert about it.
 * Over a year the daily values become faint dots under a 7-day average.
 */
export function metricLine(
  p: Palette,
  opts: { start: string; end: string; days: Point[]; band: Band; alerts: Span[]; color: string; year: boolean; unit: string },
): ChartOption {
  const dates = allDays(opts.start, opts.end);
  const byDate = new Map(opts.days);
  const values = dates.map((d) => byDate.get(d) ?? null);

  const markArea = {
    silent: true,
    data: [
      ...(opts.band
        ? [[{ yAxis: opts.band.low, itemStyle: { color: opts.color, opacity: 0.1 } }, { yAxis: opts.band.high }]]
        : []),
      ...opts.alerts.map((a) => [
        { xAxis: a.start, itemStyle: { color: p.zone5, opacity: 0.14 } },
        { xAxis: a.end },
      ]),
    ],
  };

  const series: object[] = opts.year
    ? [
        { type: "scatter", symbolSize: 3, itemStyle: { color: opts.color, opacity: 0.25 }, data: values, silent: true },
        {
          name: "ממוצע 7 ימים", type: "line", smooth: true, showSymbol: false, connectNulls: true,
          lineStyle: { width: 2, color: opts.color }, itemStyle: { color: opts.color },
          data: movingAverage(values, 7), markArea,
        },
      ]
    : [
        {
          type: "line", showSymbol: dates.length <= 31, symbolSize: 5, connectNulls: false,
          lineStyle: { width: 2, color: opts.color }, itemStyle: { color: opts.color },
          data: values, markArea,
        },
      ];

  return {
    ...base(p),
    grid: { left: 34, right: 8, top: 10, bottom: 22 },
    xAxis: axisX(p, dates),
    yAxis: axisY(p),
    tooltip: {
      ...(base(p).tooltip as object),
      valueFormatter: (v: unknown) => (v === null || v === undefined || v === "-" ? "—" : `${v} ${opts.unit}`),
    },
    series,
  };
}

const STAGES = [
  { key: "deep", label: "עמוקה", color: "#3b4fa8" },
  { key: "light", label: "קלה", color: "#7c9be6" },
  { key: "rem", label: "REM", color: "#a77be0" },
  { key: "awake", label: "ער", color: "#e9a07a" },
] as const;

export const SLEEP_STAGES = STAGES;

/** Hours by stage per night (per week, averaged, over a year) and the sleep score on its own axis. */
export function sleepStages(p: Palette, nights: SleepNight[], year: boolean): ChartOption {
  let rows = nights;
  if (year) {
    const weeks = new Map<string, SleepNight[]>();
    for (const n of nights) {
      const d = parse(n.date);
      d.setDate(d.getDate() - d.getDay());
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      weeks.set(key, [...(weeks.get(key) ?? []), n]);
    }
    const avg = (list: SleepNight[], k: "deep" | "light" | "rem" | "awake") => list.reduce((a, n) => a + n[k], 0) / list.length;
    rows = [...weeks.entries()].map(([date, list]) => {
      const scores = list.map((n) => n.score).filter((s): s is number => s !== null);
      return {
        date, deep: avg(list, "deep"), light: avg(list, "light"), rem: avg(list, "rem"), awake: avg(list, "awake"),
        score: scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null, bed: null, wake: null,
      };
    });
  }
  return {
    ...base(p),
    grid: { left: 30, right: 30, top: 34, bottom: 22 },
    legend: { top: 0, left: "center", icon: "circle", itemWidth: 9, itemHeight: 9, textStyle: { color: p.text, fontSize: 11 } },
    xAxis: { ...axisX(p, rows.map((r) => r.date)), boundaryGap: true },
    yAxis: [
      axisY(p, { scale: false, axisLabel: { color: p.muted, fontSize: 10, formatter: "{value}ש׳" } }),
      { type: "value", min: 0, max: 100, splitLine: { show: false }, axisLabel: { color: p.muted, fontSize: 10 } },
    ],
    tooltip: { ...(base(p).tooltip as object), axisPointer: { type: "shadow" } },
    series: [
      ...STAGES.map((s) => ({
        name: s.label, type: "bar", stack: "sleep", barMaxWidth: 14, itemStyle: { color: s.color, borderColor: p.card, borderWidth: 0.5 },
        tooltip: { valueFormatter: (v: unknown) => `${Number(v).toFixed(1)} ש׳` },
        data: rows.map((r) => +(r[s.key] / 60).toFixed(2)),
      })),
      {
        name: "ציון", type: "line", yAxisIndex: 1, smooth: true, showSymbol: false,
        lineStyle: { width: 2, color: p.text }, itemStyle: { color: p.text },
        data: rows.map((r) => r.score),
      },
    ],
  };
}

export const clock = (seconds: number) => {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
};

/** A predicted finish time over the year. Faster is lower, so the axis is flipped: up means better. */
export function raceTrend(p: Palette, days: Point[], color: string): ChartOption {
  return {
    ...base(p),
    grid: { left: 46, right: 8, top: 8, bottom: 22 },
    xAxis: axisX(p, days.map((d) => d[0])),
    yAxis: axisY(p, { inverse: true, axisLabel: { color: p.muted, fontSize: 10, formatter: (v: number) => clock(v) } }),
    tooltip: { ...(base(p).tooltip as object), valueFormatter: (v: unknown) => clock(Number(v)) },
    series: [{
      type: "line", smooth: true, showSymbol: false, lineStyle: { width: 2, color }, itemStyle: { color },
      areaStyle: { color, opacity: 0.06 }, data: days.map((d) => d[1]),
    }],
  };
}

export function simpleTrend(p: Palette, days: Point[], color: string): ChartOption {
  return {
    ...base(p),
    grid: { left: 34, right: 8, top: 8, bottom: 22 },
    xAxis: axisX(p, days.map((d) => d[0])),
    yAxis: axisY(p),
    series: [{ type: "line", step: "end", showSymbol: false, lineStyle: { width: 2, color }, itemStyle: { color }, data: days.map((d) => d[1]) }],
  };
}
