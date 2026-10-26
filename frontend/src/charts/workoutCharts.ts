/**
 * The four charts of the workouts tab, as ECharts options built from the
 * API's numbers and the page's palette. Pure functions: data in, option out.
 */

import { SPORTS } from "../sports";
import { base, type Palette } from "./Chart";
import type { ChartOption } from "./echarts";

export type HoursBucket = { start: string } & Record<string, number | string>;
export type LoadDay = { date: string; load: number; fitness: number; fatigue: number; form: number };
export type CalendarDay = { date: string; load: number };

const HEB_DAYS = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"];
const HEB_MONTHS = ["ינו׳", "פבר׳", "מרץ", "אפר׳", "מאי", "יוני", "יולי", "אוג׳", "ספט׳", "אוק׳", "נוב׳", "דצמ׳"];

const parse = (iso: string) => new Date(`${iso}T00:00:00`);
const dayMonth = (iso: string) => {
  const d = parse(iso);
  return `${d.getDate()}.${d.getMonth() + 1}`;
};

/** Axis labels that read naturally for each period length. */
function bucketLabel(iso: string, kind: string): string {
  const d = parse(iso);
  if (kind === "week") return HEB_DAYS[d.getDay()];
  if (kind === "month") return String(d.getDate());
  return HEB_MONTHS[d.getMonth()];
}

const axisLine = (p: Palette) => ({ lineStyle: { color: p.line } });
const splitLine = (p: Palette) => ({ lineStyle: { color: p.line, type: "dashed" as const } });

export function hoursBySport(p: Palette, buckets: HoursBucket[], kind: string): ChartOption {
  const labels = buckets.map((b) => bucketLabel(b.start, kind));
  return {
    ...base(p),
    grid: { left: 32, right: 8, top: 44, bottom: 24 },
    legend: {
      top: 0,
      left: "center",
      itemWidth: 10,
      itemHeight: 10,
      icon: "roundRect",
      textStyle: { color: p.text, fontSize: 12 },
      inactiveColor: p.skeleton,
    },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: axisLine(p),
      axisTick: { show: false },
      // A year has 53 weekly columns: label only the first week of each month.
      axisLabel: {
        color: p.muted,
        fontSize: 11,
        interval:
          kind === "year"
            ? (i: number) => i === 0 || labels[i] !== labels[i - 1]
            : kind === "month"
              ? (i: number) => i % 5 === 0
              : 0,
      },
    },
    yAxis: {
      type: "value",
      name: "שעות",
      nameTextStyle: { color: p.muted, fontSize: 11, align: "left" },
      axisLabel: { color: p.muted, fontSize: 11 },
      splitLine: splitLine(p),
    },
    tooltip: {
      ...(base(p).tooltip as object),
      axisPointer: { type: "shadow" },
      valueFormatter: (v: unknown) => (typeof v === "number" && v > 0 ? `${v.toFixed(1)} ש׳` : "—"),
    },
    series: SPORTS.map((sport) => ({
      name: sport.label,
      type: "bar",
      stack: "hours",
      barMaxWidth: 22,
      itemStyle: { color: p.sport[sport.key] },
      emphasis: { focus: "series" },
      data: buckets.map((b) => Number(b[sport.key]) || 0),
    })),
  };
}

export function fitnessFatigue(p: Palette, days: LoadDay[]): ChartOption {
  const fitnessColor = p.sport.running;
  const fatigueColor = p.dark ? "#d79b5e" : "#a8641f";
  return {
    ...base(p),
    grid: { left: 32, right: 8, top: 36, bottom: 24 },
    legend: {
      top: 0,
      left: "center",
      textStyle: { color: p.text, fontSize: 12 },
      data: ["Fitness", "Fatigue"],
    },
    xAxis: {
      type: "category",
      data: days.map((d) => d.date),
      boundaryGap: false,
      axisLine: axisLine(p),
      axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 11, formatter: dayMonth, hideOverlap: true },
    },
    yAxis: { type: "value", axisLabel: { color: p.muted, fontSize: 11 }, splitLine: splitLine(p) },
    tooltip: { ...(base(p).tooltip as object), valueFormatter: (v: unknown) => Number(v).toFixed(1) },
    series: [
      {
        name: "Fitness",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2.5, color: fitnessColor },
        itemStyle: { color: fitnessColor },
        areaStyle: { color: fitnessColor, opacity: 0.08 },
        data: days.map((d) => d.fitness),
      },
      {
        name: "Fatigue",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color: fatigueColor },
        itemStyle: { color: fatigueColor },
        data: days.map((d) => d.fatigue),
      },
    ],
  };
}

/** Below -30 is the high-risk line - those bars turn red, as in the video. */
export const FORM_RISK = -30;

export function form(p: Palette, days: LoadDay[]): ChartOption {
  return {
    ...base(p),
    grid: { left: 32, right: 8, top: 12, bottom: 24 },
    xAxis: {
      type: "category",
      data: days.map((d) => d.date),
      axisLine: axisLine(p),
      axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 11, formatter: dayMonth, hideOverlap: true },
    },
    yAxis: { type: "value", axisLabel: { color: p.muted, fontSize: 11 }, splitLine: splitLine(p) },
    tooltip: {
      ...(base(p).tooltip as object),
      axisPointer: { type: "shadow" },
      valueFormatter: (v: unknown) => Number(v).toFixed(1),
    },
    series: [
      {
        name: "Form",
        type: "bar",
        barMaxWidth: 10,
        data: days.map((d) => ({
          value: d.form,
          itemStyle: { color: d.form < FORM_RISK ? p.zone5 : d.form >= 0 ? p.sport.swimming : p.accent },
        })),
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: p.zone5, type: "dashed", width: 1 },
          label: { color: p.zone5, fontSize: 10, formatter: "-30" },
          data: [{ yAxis: FORM_RISK }],
        },
      },
    ],
  };
}

export function yearCalendar(p: Palette, start: string, end: string, days: CalendarDay[], width: number): ChartOption {
  const max = Math.max(60, ...days.map((d) => d.load));
  // 53 week columns across whatever width the phone has.
  const cell = Math.max(5, Math.floor((width - 24) / 54));
  return {
    tooltip: {
      backgroundColor: p.card,
      borderColor: p.line,
      textStyle: { color: p.text, fontSize: 12 },
      formatter: (params: unknown) => {
        const [date, load] = (params as { value: [string, number] }).value;
        return `${dayMonth(date)}: עומס ${load}`;
      },
    },
    visualMap: {
      show: false,
      min: 0,
      max,
      inRange: { color: [p.skeleton, p.dark ? "#3c5ba8" : "#a9bff2", p.accent] },
    },
    calendar: {
      range: [start, end],
      top: 18,
      left: 12,
      right: 12,
      cellSize: [cell, cell],
      orient: "horizontal",
      splitLine: { show: false },
      itemStyle: { color: "transparent", borderColor: p.card, borderWidth: 1.5 },
      yearLabel: { show: false },
      dayLabel: { show: false },
      monthLabel: { color: p.muted, fontSize: 10, nameMap: HEB_MONTHS, margin: 4 },
    },
    series: [{ type: "heatmap", coordinateSystem: "calendar", data: days.map((d) => [d.date, d.load]) }],
  };
}
