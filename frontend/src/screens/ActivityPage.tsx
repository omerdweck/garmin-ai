import { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet } from "../api";
import { Chart, type Palette } from "../charts/Chart";
import { CHART_GROUP, lengthsChart, metricChart, paceOf, STROKE_LABEL, xAxisOf, type Length, type Points, type Series } from "../charts/activityCharts";
import { GlassSegmented } from "../components/GlassSegmented";
import { RouteMap } from "../components/RouteMap";
import { EmptyState } from "../components/ui";
import { duration, fmt, pace, when } from "../format";
import { useNav } from "../nav";
import { SPORTS } from "../sports";
import "./activity.css";

type Split = { index: number; meters: number; seconds: number; pace: number; avg_hr: number | null; gain: number };
type Detail = {
  points: Points; track: [number, number][]; splits: Split[]; split_meters: number;
  laps: unknown[]; lengths: Length[]; zones: { zone: number; seconds: number; low: number | null }[]; drift: number | null;
};
type Activity = {
  id: number; name: string; sport: string; type: string; start: string; seconds: number; meters: number;
  avg_hr: number | null; max_hr: number | null; calories: number | null; manual: boolean;
  extra: Record<string, number | string>;
  sets: { exercise: string | null; muscle: string | null; reps: number | null; kg: number | null; seconds: number }[];
  detail: Detail | null;
  status: "ready" | "pending" | "busy" | "manual";
};

type Tab = "metrics" | "splits" | "charts" | "zones";

const POLL_MS = 4000;
const POLL_LIMIT = 30; // two minutes
const ACCENT = "#F07B3F";
const ZONE_COLORS = ["#9aa0a6", "#3b82f6", "#22a861", "#f08c2a", "#e5484d"];
const sportOf = (key: string) => SPORTS.find((s) => s.key === key) ?? SPORTS[SPORTS.length - 1];

function useActivity(id: number) {
  const [data, setData] = useState<Activity | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    let tries = 0;
    let timer: number | undefined;
    const load = async () => {
      try {
        const body = await apiGet<Activity>(`/api/activities/${id}`);
        if (!alive) return;
        setData(body);
        // The detail is being fetched from Garmin by the worker: ask again shortly.
        if (body.status === "pending" && ++tries < POLL_LIMIT) timer = window.setTimeout(load, POLL_MS);
      } catch {
        if (alive) setError(true);
      }
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [id]);
  return { data, error };
}

function Stat({ value, unit, label }: { value: string; unit?: string; label: string }) {
  return (
    <div className="hero-stat">
      <div className="hero-value"><span className="num">{value}</span>{unit && <small>{unit}</small>}</div>
      <div className="hero-label">{label}</div>
    </div>
  );
}

/** The four numbers in the dark card: what matters most for this kind of workout. */
function heroStats(a: Activity): { value: string; unit?: string; label: string }[] {
  const moving = Number(a.extra.moving_seconds) || a.seconds;
  const speed = a.meters && moving ? a.meters / moving : null;
  const time = { value: duration(moving), label: "משך זמן" };
  const kcal = { value: a.calories ? fmt(a.calories) : "—", unit: "kcal", label: "קלוריות" };
  if (a.sport === "swimming") {
    return [
      { value: fmt(a.meters), unit: "m", label: "מרחק" }, time,
      { value: pace(speed, 100), unit: "/100m", label: "קצב ממוצע" }, kcal,
    ];
  }
  if (a.sport === "cycling") {
    return [
      { value: fmt(a.meters / 1000, 1), unit: "km", label: "מרחק" }, time,
      { value: speed ? fmt(speed * 3.6, 1) : "—", unit: "km/h", label: "מהירות ממוצעת" }, kcal,
    ];
  }
  if (a.meters > 0) {
    return [
      { value: fmt(a.meters / 1000, 2), unit: "km", label: "מרחק" }, time,
      { value: pace(speed), unit: "/km", label: "קצב ממוצע" }, kcal,
    ];
  }
  return [
    time, kcal,
    { value: a.avg_hr ? String(a.avg_hr) : "—", unit: "bpm", label: "דופק ממוצע" },
    { value: a.extra.sets ? String(a.extra.sets) : a.max_hr ? String(a.max_hr) : "—", label: a.extra.sets ? "סטים" : "דופק מקסימלי" },
  ];
}

function MetricCard({ icon, title, left, right, points, series, onPointer }: {
  icon: string; title: string;
  left: [string, string]; right: [string, string];
  points: Points; series: Series; onPointer: (i: number | null) => void;
}) {
  const build = useCallback((p: Palette) => metricChart(p, points, series), [points, series]);
  return (
    <section className="act-card">
      <div className="act-card-head"><h3>{icon} {title}</h3></div>
      <div className="act-pair">
        <div><div className="act-small">{left[0]}</div><div className="act-big">{left[1]}</div></div>
        <div><div className="act-small">{right[0]}</div><div className="act-big">{right[1]}</div></div>
      </div>
      <Chart build={build} height={130} group={CHART_GROUP} onPointer={onPointer} />
    </section>
  );
}

/** One chart in the "all together" tab; memoized so moving the cursor doesn't redraw it. */
function ChartRow({ label, points, series, height, onPointer }: {
  label: string; points: Points; series: Series; height: number; onPointer: (i: number | null) => void;
}) {
  const build = useCallback((p: Palette) => metricChart(p, points, series), [points, series]);
  return (
    <>
      <div className="act-small">{label}</div>
      <Chart build={build} height={height} group={CHART_GROUP} onPointer={onPointer} />
    </>
  );
}

function SplitsTable({ splits, unit, sport }: { splits: Split[]; unit: number; sport: string }) {
  if (!splits.length) return <p className="act-empty">אין splits לאימון הזה.</p>;
  const paces = splits.map((s) => s.pace);
  const fastest = Math.min(...paces);
  const slowest = Math.max(...paces);
  const avg = paces.reduce((a, b) => a + b, 0) / paces.length;
  const label = sport === "cycling" ? "קמ״ש" : "קצב";
  return (
    <section className="act-card">
      <div className="act-card-head"><h3>⏱ {unit === 1000 ? "לכל ק״מ" : `לכל ${unit / 1000} ק״מ`}</h3></div>
      <p className="act-caption">ירוק: מהר מהממוצע שלך באימון. כתום: לאט ממנו.</p>
      <div className="splits">
        <div className="split-row split-head"><span>{unit === 1000 ? "ק״מ" : "קטע"}</span><span>{label}</span><span /><span>דופק</span></div>
        {splits.map((s) => {
          const width = 30 + 70 * (slowest === fastest ? 1 : (slowest - s.pace) / (slowest - fastest));
          const value = sport === "cycling" ? fmt((unit / s.pace) * 3.6, 1) : duration(s.pace);
          return (
            <div key={s.index} className="split-row">
              <span className="num">{s.meters < unit ? fmt(s.meters / 1000, 2) : s.index}</span>
              <span className="num">{value}</span>
              <span className="split-bar"><i style={{ width: `${width}%`, background: s.pace <= avg ? "#1ba39c" : ACCENT }} /></span>
              <span className="num act-muted">{s.avg_hr ?? "—"}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Zones({ zones, drift }: { zones: Detail["zones"]; drift: number | null }) {
  const total = zones.reduce((a, z) => a + z.seconds, 0);
  return (
    <>
      <section className="act-card">
        <div className="act-card-head"><h3>❤️ זמן באזורי דופק</h3></div>
        {total === 0 ? <p className="act-empty">אין נתוני דופק לאימון הזה.</p> : zones.map((z) => (
          <div key={z.zone} className="zone-row">
            <span className="zone-name">אזור {z.zone}{z.low ? <small className="num"> {z.low}+</small> : null}</span>
            <span className="zone-bar"><i style={{ width: `${(100 * z.seconds) / total}%`, background: ZONE_COLORS[z.zone - 1] }} /></span>
            <span className="num zone-time">{duration(z.seconds)}</span>
          </div>
        ))}
      </section>
      {drift !== null && (
        <section className="act-card">
          <div className="act-card-head"><h3>📈 Cardiac drift</h3></div>
          <div className="act-big" style={{ color: drift > 5 ? ACCENT : "#1ba39c" }}>{drift > 0 ? "+" : ""}{drift}%</div>
          <p className="act-caption">
            כמה יותר דופק עלתה אותה מהירות בחצי השני לעומת הראשון. עד 5%: אירובי יציב. מעל 5%: עייפות, חום או חוסר נוזלים.
          </p>
        </section>
      )}
    </>
  );
}

function Lengths({ lengths }: { lengths: Length[] }) {
  const build = useCallback((p: Palette) => lengthsChart(p, lengths), [lengths]);
  const strokes = [...new Set(lengths.map((l) => l.stroke).filter(Boolean))] as string[];
  const swolf = lengths.map((l) => l.swolf).filter((v): v is number => v != null);
  return (
    <section className="act-card">
      <div className="act-card-head"><h3>🏊 בריכות</h3></div>
      <div className="act-pair">
        <div><div className="act-small">בריכות</div><div className="act-big">{lengths.length}</div></div>
        <div><div className="act-small">SWOLF ממוצע</div><div className="act-big">{swolf.length ? Math.round(swolf.reduce((a, b) => a + b, 0) / swolf.length) : "—"}</div></div>
      </div>
      <Chart build={build} height={160} />
      <p className="act-caption">עמודה לכל בריכה (זמן), צבועה לפי סגנון; הקו: SWOLF, זמן + תנועות. נמוך יותר = יעיל יותר.{strokes.length ? ` סגנונות: ${strokes.map((s) => STROKE_LABEL[s] ?? s).join(", ")}.` : ""}</p>
    </section>
  );
}

function Sets({ sets }: { sets: Activity["sets"] }) {
  if (!sets.length) return <p className="act-empty">Garmin לא זיהתה סטים באימון הזה.</p>;
  return (
    <section className="act-card">
      <div className="act-card-head"><h3>🏋️ סטים</h3></div>
      <ul className="sets">
        {sets.map((s, i) => (
          <li key={i}>
            <span className="set-n num">{i + 1}</span>
            <span className="set-name">{s.exercise ?? "תרגיל לא מזוהה"}{s.muscle && <small> · {s.muscle}</small>}</span>
            <span className="num">{s.reps ? `${s.reps}×` : duration(s.seconds)}{s.kg ? ` ${s.kg} ק״ג` : ""}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Summary({ a }: { a: Activity }) {
  const rows: [string, string][] = [];
  const e = a.extra;
  if (e.elevation_gain != null) rows.push(["טיפוס", `${fmt(Number(e.elevation_gain))} מ׳`]);
  if (e.cadence) rows.push(["קדנס ממוצע", `${fmt(Number(e.cadence))} צעדים/דק׳`]);
  if (e.avg_power) rows.push(["הספק ממוצע", `${fmt(Number(e.avg_power))} W`]);
  if (e.aerobic_te != null) rows.push(["Training Effect", `${Number(e.aerobic_te).toFixed(1)} אירובי · ${Number(e.anaerobic_te ?? 0).toFixed(1)} אנאירובי`]);
  if (e.load != null) rows.push(["עומס האימון", fmt(Number(e.load))]);
  if (!rows.length) return null;
  return (
    <section className="act-card">
      <ul className="act-list">{rows.map(([k, v]) => <li key={k}><span>{k}</span><span className="act-value">{v}</span></li>)}</ul>
    </section>
  );
}

export function ActivityPage({ id }: { id: number }) {
  const { data: a, error } = useActivity(id);
  const nav = useNav();
  const [tab, setTab] = useState<Tab>("metrics");
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [bigMap, setBigMap] = useState(false);

  const d = a?.detail ?? null;
  const points = d?.points;
  const sport = a ? sportOf(a.sport) : null;

  const onPointer = useCallback((index: number | null) => {
    if (index === null || !points) return setCursor(null);
    const lat = points.lat[index];
    const lon = points.lon[index];
    setCursor(lat != null && lon != null ? [lat, lon] : null);
  }, [points]);

  const series = useMemo(() => {
    if (!points) return null;
    const paces = points.speed.map(paceOf);
    return {
      hr: { values: points.hr, color: "#e5484d", format: (v: number) => `${Math.round(v)}` } as Series,
      pace: { values: paces, color: "#3b6fe0", inverse: true, format: (v: number) => duration(v) } as Series,
      speed: { values: points.speed.map((s) => (s != null ? +(s * 3.6).toFixed(1) : null)), color: "#3b6fe0", format: (v: number) => `${v}` } as Series,
      elev: { values: points.elev, color: "#1ba39c", format: (v: number) => `${Math.round(v)}` } as Series,
      cad: { values: points.cad, color: "#7b5cd6", format: (v: number) => `${Math.round(v)}`, area: false } as Series,
      power: { values: points.power, color: "#e0a21b", format: (v: number) => `${Math.round(v)}` } as Series,
    };
  }, [points]);

  if (error) {
    return (
      <div className="act-page">
        <EmptyState
          card icon="watch" title="האימון לא נטען"
          text="ייתכן שהוא נמחק ב-Garmin, או שהחיבור נקטע לרגע."
          action={{ label: "לרשימת האימונים", run: () => nav.openTab("workouts", "workouts-list") }}
        />
      </div>
    );
  }
  if (!a || !sport) return <div className="act-page"><div className="skeleton" style={{ height: 420, borderRadius: 24 }} /></div>;

  const isSwim = a.sport === "swimming";
  const isStrength = a.type === "strength_training";
  const tabs: [Tab, string][] = isStrength
    ? [["metrics", "מדדים"], ["zones", "אזורי דופק"]]
    : [["metrics", "מדדים"], ["splits", isSwim ? "בריכות" : "קצב"], ["charts", "גרפים"], ["zones", "אזורי דופק"]];
  const hrValues = points?.hr.filter((v): v is number => v != null) ?? [];
  const paceValues = series?.pace.values.filter((v): v is number => v != null) ?? [];
  const elevGain = Number(a.extra.elevation_gain ?? 0);
  const x = points ? xAxisOf(points) : null;

  return (
    <div className="act-page">
      <header className="act-top">
        <h1>פרטי אימון</h1>
        <div className="act-date">{when(a.start)}</div>
      </header>

      <section className="hero">
        <div className="hero-head">
          <span className="hero-icon" style={{ background: ACCENT }}>{sport.icon}</span>
          <div>
            <div className="hero-title">{a.name || sport.label}{a.manual ? " ✍️" : ""}</div>
            <div className="hero-sub">{String(a.extra.te_label ?? "").replace(/_/g, " ").toLowerCase() || sport.label}</div>
          </div>
        </div>
        <div className="hero-grid">{heroStats(a).map((s) => <Stat key={s.label} {...s} />)}</div>
        {d && d.track.length > 1 && (
          <div className="hero-map">
            <RouteMap track={d.track} color={ACCENT} cursor={cursor} height={bigMap ? 380 : 190} />
            <button className="map-expand" onClick={() => setBigMap((b) => !b)} aria-label={bigMap ? "הקטנת המפה" : "הגדלת המפה"}>
              {bigMap ? "⤡" : "⤢"}
            </button>
          </div>
        )}
      </section>

      {a.status === "manual" && <p className="act-empty">אימון שהוזן ידנית: אין לו מפה וגרפים, רק את מה שהוזן.</p>}
      {a.status === "pending" && (
        <div className="act-loading"><div className="skeleton" style={{ height: 150, borderRadius: 20 }} /><p>טוען את פרטי האימון מ-Garmin…</p></div>
      )}
      {a.status === "busy" && <p className="act-empty">Garmin עמוסה כרגע. נסה לפתוח שוב בעוד כמה דקות.</p>}

      {(d || isStrength) && (
        <>
          <GlassSegmented<Tab>
            className="act-tabs"
            role="tablist"
            label="תצוגת האימון"
            options={tabs.map(([key, label]) => ({ key, label }))}
            value={tab}
            onChange={setTab}
          />

          {tab === "metrics" && (
            <>
              {isStrength && <Sets sets={a.sets} />}
              {points && series && hrValues.length > 0 && (
                <MetricCard icon="❤️" title="דופק" points={points} series={series.hr} onPointer={onPointer}
                  left={["ממוצע", `${a.avg_hr ?? Math.round(hrValues.reduce((s, v) => s + v, 0) / hrValues.length)} bpm`]}
                  right={["מקסימום", `${a.max_hr ?? Math.max(...hrValues)} bpm`]} />
              )}
              {points && series && !isSwim && !isStrength && paceValues.length > 0 && (
                a.sport === "cycling" ? (
                  <MetricCard icon="🚴" title="מהירות" points={points} series={series.speed} onPointer={onPointer}
                    left={["ממוצעת", `${fmt((a.meters / (Number(a.extra.moving_seconds) || a.seconds)) * 3.6, 1)} קמ״ש`]}
                    right={["מקסימלית", `${fmt(Number(a.extra.max_speed ?? 0) * 3.6, 1)} קמ״ש`]} />
                ) : (
                  <MetricCard icon="⏱" title="קצב" points={points} series={series.pace} onPointer={onPointer}
                    left={["ממוצע", `${pace(a.meters / (Number(a.extra.moving_seconds) || a.seconds))} /km`]}
                    right={["מהיר ביותר", `${duration(Math.min(...paceValues))} /km`]} />
                )
              )}
              {points && series && points.elev.some((v) => v != null) && x?.byDistance && (
                <MetricCard icon="⛰️" title="גובה" points={points} series={series.elev} onPointer={onPointer}
                  left={["טיפוס", `${fmt(elevGain)} מ׳`]} right={["ירידה", `${fmt(Number(a.extra.elevation_loss ?? 0))} מ׳`]} />
              )}
              {points && series && points.power.some((v) => v != null) && (
                <MetricCard icon="⚡" title="הספק" points={points} series={series.power} onPointer={onPointer}
                  left={["ממוצע", `${a.extra.avg_power ?? "—"} W`]} right={["מנורמל", `${a.extra.norm_power ?? "—"} W`]} />
              )}
              <Summary a={a} />
            </>
          )}

          {tab === "splits" && d && (isSwim ? <Lengths lengths={d.lengths} /> : <SplitsTable splits={d.splits} unit={d.split_meters} sport={a.sport} />)}

          {tab === "charts" && points && series && (
            <section className="act-card">
              <div className="act-card-head"><h3>📊 הכל ביחד</h3></div>
              <p className="act-caption">נגיעה בגרף מזיזה את הסמן בכל הגרפים ועל המפה. {x?.byDistance ? "ציר: ק״מ." : "ציר: דקות."}</p>
              {hrValues.length > 0 && <ChartRow label="דופק" points={points} series={series.hr} height={110} onPointer={onPointer} />}
              {paceValues.length > 0 && !isSwim && (
                <ChartRow label={a.sport === "cycling" ? "מהירות" : "קצב"} points={points}
                  series={a.sport === "cycling" ? series.speed : series.pace} height={110} onPointer={onPointer} />
              )}
              {points.elev.some((v) => v != null) && <ChartRow label="גובה" points={points} series={series.elev} height={110} onPointer={onPointer} />}
              {points.cad.some((v) => v != null) && <ChartRow label="קדנס" points={points} series={series.cad} height={100} onPointer={onPointer} />}
            </section>
          )}

          {tab === "zones" && <Zones zones={d?.zones ?? []} drift={d?.drift ?? null} />}
        </>
      )}

      {a.manual && (
        <p className="act-caption" style={{ textAlign: "center" }}>אימון ידני אפשר למחוק בבוט: 🏃 האימונים שלי ← האימון ← 🗑</p>
      )}
    </div>
  );
}
