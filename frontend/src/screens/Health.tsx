import { useCallback } from "react";
import { Chart, type Palette } from "../charts/Chart";
import { metricLine, sleepStages, SLEEP_STAGES, type Band, type Point, type SleepNight, type Span } from "../charts/healthCharts";
import { HistoryBanner } from "../components/HistoryBanner";
import { RangeSelector } from "../components/RangeSelector";
import { Card, ChartPlaceholder, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useRange } from "../range";

type Metric = { days: Point[]; last_week: number | null; band: Band; alerts: Span[] };
type Health = {
  start: string; end: string; days_recorded: number; since: string | null;
  metrics: Record<string, Metric>; sleep: SleepNight[]; sleep_last_week_hours: number | null; sleep_alerts: Span[];
};

// Order, wording and color of each card. The explanation line is what makes
// a chart readable without knowing what the metric is (plan, part C4).
const METRICS: { key: string; name: string; caption: string; unit: string; color: string; digits?: number }[] = [
  { key: "resting_heart_rate", name: "דופק מנוחה", caption: "ממוצע 30 הדקות הנמוכות בכל יום. נמוך יותר = לב יעיל יותר.", unit: "bpm", color: "var(--sport-cycling)" },
  { key: "avg_hrv", name: "HRV", caption: "השונות בין פעימות בשינה. גבוה = התאוששות טובה.", unit: "ms", color: "var(--sport-running)" },
  { key: "avg_stress_level", name: "Stress", caption: "ציון 0–100 של Garmin, מ-HRV בזמן מנוחה.", unit: "", color: "var(--sport-other)" },
  { key: "body_battery_high", name: "שיא Body Battery", caption: "הטעינה הגבוהה ביותר בכל יום.", unit: "/100", color: "var(--sport-swimming)" },
  { key: "intensity_minutes", name: "דקות עצימות", caption: "מתונה, ועצימה כפול 2. ההמלצה: 150 בשבוע.", unit: "דק׳", color: "var(--sport-strength)" },
  { key: "respiration", name: "נשימה", caption: "נשימות לדקה בממוצע, בשינה.", unit: "/דק׳", color: "var(--sport-walking)", digits: 1 },
  { key: "spo2", name: "SpO2", caption: "רוויית חמצן בדם בשינה.", unit: "%", color: "var(--accent)" },
];

const fullDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("he-IL", { day: "numeric", month: "long", year: "numeric" });

/** CSS variables can't go into a canvas: resolve "var(--x)" to its color. */
function resolve(p: Palette, color: string): string {
  const name = color.match(/--sport-(\w+)/)?.[1];
  if (name && p.sport[name]) return p.sport[name];
  return color.includes("--accent") ? p.accent : color;
}

function MetricCard({ data, meta, start, end, year }: {
  data: Metric; meta: (typeof METRICS)[number]; start: string; end: string; year: boolean;
}) {
  const build = useCallback(
    (p: Palette) => metricLine(p, { start, end, days: data.days, band: data.band, alerts: data.alerts, color: resolve(p, meta.color), year, unit: meta.unit }),
    [data, meta, start, end, year],
  );
  const lastWeek = data.last_week;
  return (
    <section className="card">
      <div className="metric-head">
        <div>
          <h2 className="card-title">{meta.name}</h2>
          <p className="card-caption">{meta.caption}</p>
        </div>
        <div className="metric-value">
          <div className="kicker">השבוע שעבר</div>
          <div className="stat-value num" style={{ color: meta.color }}>
            {lastWeek === null ? "—" : lastWeek.toFixed(meta.digits ?? 0)}
          </div>
          <div className="stat-sub">{meta.unit}</div>
        </div>
      </div>
      <Chart build={build} height={130} />
      <div className="chart-notes">
        {data.band && (
          <span><i className="swatch" style={{ background: meta.color, opacity: 0.25 }} />הנורמה שלך ({data.band.low}–{data.band.high})</span>
        )}
        {data.alerts.length > 0 && <span><i className="swatch" style={{ background: "var(--zone-5)", opacity: 0.3 }} />התראה מהמאמן</span>}
      </div>
    </section>
  );
}

function SleepCard({ data, year }: { data: Health; year: boolean }) {
  const build = useCallback((p: Palette) => sleepStages(p, data.sleep, year), [data, year]);
  const last = data.sleep.at(-1);
  return (
    <section className="card">
      <div className="metric-head">
        <div>
          <h2 className="card-title">שינה</h2>
          <p className="card-caption">{year ? "ממוצע לילה בכל שבוע, לפי שלבים." : "שעות בכל לילה לפי שלבים, והקו: ציון השינה."}</p>
        </div>
        <div className="metric-value">
          <div className="kicker">השבוע שעבר</div>
          <div className="stat-value num">{data.sleep_last_week_hours ?? "—"}</div>
          <div className="stat-sub">שעות בלילה</div>
        </div>
      </div>
      {data.sleep.length ? <Chart build={build} height={200} /> : <p className="stat-sub">אין נתוני שינה בטווח הזה.</p>}
      {last?.bed && last.wake && (
        <p className="stat-sub" style={{ marginTop: 8 }}>
          הלילה האחרון: נרדמת ב-<span className="num">{last.bed}</span>, קמת ב-<span className="num">{last.wake}</span>
          {last.score !== null && <> · ציון <span className="num">{last.score}</span></>}
        </p>
      )}
      <div className="chart-notes">
        {SLEEP_STAGES.map((s) => <span key={s.key}><i className="swatch" style={{ background: s.color }} />{s.label}</span>)}
      </div>
    </section>
  );
}

export function Health() {
  const range = useRange();
  const { data, error } = useApi<Health>(`/api/health-metrics?kind=${range.kind}&offset=${range.offset}`);
  const year = range.kind === "year";

  return (
    <>
      <PageHeader
        kicker="בריאות ושינה"
        title="כל יום, כל היום"
        subtitle={data?.since
          ? <><span className="num">{data.days_recorded.toLocaleString("he-IL")}</span> ימים מתועדים, מאז {fullDate(data.since)}</>
          : "הסיכום היומי של השעון, יום אחרי יום."}
      />
      <HistoryBanner />
      <RangeSelector />
      <div className="grid">
        {!data && !error && METRICS.slice(0, 4).map((m) => <Card key={m.key} title={m.name} caption={m.caption}><ChartPlaceholder height={130} /></Card>)}
        {error && <p className="stat-sub">הנתונים לא נטענו. נסה לסגור ולפתוח שוב.</p>}
        {data && <SleepCard data={data} year={year} />}
        {data && METRICS.filter((m) => data.metrics[m.key]?.days.length).map((m) => (
          <MetricCard key={m.key} data={data.metrics[m.key]} meta={m} start={data.start} end={data.end} year={year} />
        ))}
      </div>
    </>
  );
}
