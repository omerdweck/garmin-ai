import { useEffect, useRef, useState } from "react";
import { echarts, type ChartOption } from "./echarts";

/** The page's current colors, read from the CSS tokens in theme.css. */
export type Palette = {
  text: string;
  muted: string;
  line: string;
  card: string;
  skeleton: string;
  accent: string;
  warn: string;
  sport: Record<string, string>;
  zone5: string;
  dark: boolean;
};

function readPalette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    text: v("--text"),
    muted: v("--muted"),
    line: v("--line"),
    card: v("--card"),
    skeleton: v("--skeleton"),
    accent: v("--accent"),
    warn: v("--warn"),
    zone5: v("--zone-5"),
    dark: document.documentElement.dataset.theme === "dark",
    sport: {
      running: v("--sport-running"),
      cycling: v("--sport-cycling"),
      swimming: v("--sport-swimming"),
      strength: v("--sport-strength"),
      walking: v("--sport-walking"),
      other: v("--sport-other"),
    },
  };
}

/** Re-reads the palette whenever Telegram switches light/dark (main.tsx sets data-theme). */
function usePalette(): Palette {
  const [palette, setPalette] = useState(readPalette);
  useEffect(() => {
    const observer = new MutationObserver(() => setPalette(readPalette()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return palette;
}

/**
 * One ECharts chart. `build` turns the palette into the chart's option, so
 * every chart repaints itself on a theme change.
 *
 * Wrapped in dir="ltr": time runs left to right even on a Hebrew page.
 */
export function Chart({ build, height }: { build: (p: Palette) => ChartOption; height: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<ReturnType<typeof echarts.init> | null>(null);
  const palette = usePalette();

  useEffect(() => {
    if (!ref.current) return;
    const instance = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(build(palette), { notMerge: true });
  }, [build, palette]);

  return <div dir="ltr" ref={ref} style={{ height, width: "100%" }} />;
}

/** Defaults every chart shares: font, tooltip look, no animation on first paint for slow phones. */
export function base(p: Palette): ChartOption {
  return {
    textStyle: { fontFamily: "Heebo, system-ui, sans-serif", color: p.muted },
    animationDuration: 400,
    tooltip: {
      trigger: "axis",
      backgroundColor: p.card,
      borderColor: p.line,
      textStyle: { color: p.text, fontSize: 12 },
      axisPointer: { type: "line", lineStyle: { color: p.muted, type: "dashed" } },
    },
  };
}
