import { useState, type CSSProperties, type KeyboardEvent } from "react";
import { Icon } from "../components/icons";
import { LoadGauge, type LoadBalanceData } from "../components/LoadBalance";
import { Rings } from "../components/Rings";
import { PageHeader } from "../components/ui";
import { distance, duration, fmt } from "../format";
import { useApi } from "../hooks/useApi";
import { useNav } from "../nav";
import { SPORTS } from "../sports";
import "./home.css";

/*
 * The home screen answers three questions in five seconds, top to bottom:
 * what's my state today (the dark readiness card and its coach line), what
 * do I do today (the workout and the week), and how am I progressing (the
 * activity rings and the load balance). Every card leads to the tab that
 * explains it in full.
 */

type Ring = {
  key: string; label: string; value: number | null; as_of: string | null;
  fill: number; state: "good" | "normal" | "low" | "unknown";
  band: { mean: number; low: number; high: number } | null;
};
type Block = { kind: string; seconds: number; label?: string };
type Planned = { name: string; sport: string; on_watch: boolean; blocks: Block[] };
type WeekDay = {
  date: string;
  planned: { name: string; sport: string; on_watch: boolean }[];
  done: { name: string; sport: string; minutes: number }[];
};
type ActivityRings = {
  day: { steps: number; step_goal: number };
  week: {
    steps: number; step_goal: number;
    intensity_minutes: number; intensity_goal: number;
    workouts: number; workouts_goal: number;
  };
  streak_weeks: number;
};
type TodayData = {
  rings: Ring[];
  readiness_score: number | null;
  coach_line: string | null;
  workouts: Planned[];
  today_done: { id: number; name: string; sport: string; seconds: number; meters: number | null }[];
  next_workout: { name: string; sport: string; date: string; on_watch: boolean } | null;
  week: WeekDay[];
  activity_rings: ActivityRings;
  load_balance: LoadBalanceData | null;
};

const STATE_COLOR = { good: "#4fd1a1", normal: "#f5f3ef", low: "#ff8a7a", unknown: "rgba(245,243,239,0.5)" };
const STATE_WORD = { good: "מעל הרגיל", normal: "בטווח הרגיל", low: "מתחת לרגיל", unknown: "אין נתון" };
// Each readiness signal opens its own card on the health tab.
const SIGNAL_FOCUS: Record<string, string> = {
  body_battery: "metric-body_battery_high",
  hrv: "metric-avg_hrv",
  sleep: "metric-sleep",
  rhr: "metric-resting_heart_rate",
};
const DAY_NAMES = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"];

const sportOf = (key: string) => SPORTS.find((s) => s.key === key) ?? SPORTS[SPORTS.length - 1];
const isoOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayIso = () => isoOf(new Date());

/** A whole card as one tap target, with the keyboard behaving like a button's. */
function tappable(onTap: () => void) {
  return {
    role: "button",
    tabIndex: 0,
    onClick: onTap,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onTap();
      }
    },
  };
}

function greeting(hour: number): string {
  if (hour < 5) return "לילה טוב";
  if (hour < 12) return "בוקר טוב";
  if (hour < 17) return "צהריים טובים";
  if (hour < 21) return "ערב טוב";
  return "לילה טוב";
}

function readinessWords(score: number): string {
  if (score >= 75) return "מוכן לאימון קשה";
  if (score >= 50) return "מוכנות טובה";
  if (score >= 25) return "יום מתון";
  return "יום התאוששות";
}

// --- 1. readiness -------------------------------------------------------------

/**
 * The dark card: Garmin's readiness score, the coach's one sentence, and
 * the four signals - each a button into the health tab. Built from the
 * hero-card classes directly rather than HeroCard, whose stats aren't
 * tappable.
 */
function Readiness({ data }: { data: TodayData | null }) {
  const nav = useNav();
  if (!data) return <div className="skeleton" style={{ height: 270, borderRadius: 24 }} />;
  const score = data.readiness_score;
  const pct = score == null ? null : Math.max(0, Math.min(100, Math.round(score)));
  return (
    <section className="hero-card home-hero">
      <div className="hero-kicker">מוכנות להיום</div>
      <div className="hero-title">{score != null ? readinessWords(score) : "איך הגוף שלך היום"}</div>
      <div className="hero-sub">
        {score != null ? <>ציון המוכנות של Garmin: <span className="num">{score}/100</span></> : "ארבעת המדדים מול הנורמה האישית שלך"}
      </div>
      {pct !== null && (
        <div className="hero-progress">
          <span className="num">{pct}%</span>
          <div className="track" dir="ltr"><span style={{ width: `${Math.max(pct, 2)}%` }} /></div>
        </div>
      )}
      {data.coach_line && (
        <p className="coach-line">
          <span className="coach-icon"><Icon name="zap" size={15} stroke={2.2} /></span>
          {data.coach_line}
        </p>
      )}
      <div className="hero-stats">
        {data.rings.map((r) => {
          const stale = r.value !== null && r.as_of !== todayIso();
          return (
            <button key={r.key} type="button" onClick={() => nav.openTab("health", SIGNAL_FOCUS[r.key])}>
              <div className="value"><span className="num">{r.value ?? "—"}</span></div>
              <div className="label">{r.label + (stale ? " · אתמול" : "")}</div>
              <div className="state" style={{ color: STATE_COLOR[r.state] }}>{STATE_WORD[r.state]}</div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// --- 2. today's workout and the week ---------------------------------------------

// Intensity strip: taller and warmer for harder blocks.
const BLOCK_STYLE: Record<string, { h: number; color: string }> = {
  warmup: { h: 40, color: "var(--zone-2)" },
  interval: { h: 100, color: "var(--zone-5)" },
  recovery: { h: 30, color: "var(--zone-1)" },
  rest: { h: 18, color: "var(--zone-1)" },
  cooldown: { h: 40, color: "var(--zone-2)" },
  other: { h: 55, color: "var(--zone-3)" },
};

function PlannedWorkout({ workout }: { workout: Planned }) {
  const sport = sportOf(workout.sport);
  const total = workout.blocks.reduce((a, b) => a + b.seconds, 0);
  return (
    <div className="inner-box">
      <div className="workout-row">
        <span className="icon-circle lg" style={{ "--tone": "var(--orange)" } as CSSProperties}>{sport.icon}</span>
        <div style={{ minWidth: 0 }}>
          <div className="workout-name">{workout.name}</div>
          <div className="stat-sub">
            {sport.label}
            {total > 0 && <> · כ-<span className="num">{Math.round(total / 60)}</span> דקות</>}
          </div>
        </div>
      </div>
      {total > 0 && (
        <div className="blocks" dir="ltr" aria-hidden="true">
          {workout.blocks.map((b, i) => {
            const style = BLOCK_STYLE[b.kind] ?? BLOCK_STYLE.other;
            return <span key={i} style={{ flex: b.seconds, height: `${style.h}%`, background: style.color }} />;
          })}
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <span className={`pill${workout.on_watch ? " dark" : ""}`}>
          <Icon name="watch" size={14} stroke={2} />
          {workout.on_watch ? "נמצא בשעון" : "שמור בבוט, עוד לא בשעון"}
        </span>
      </div>
    </div>
  );
}

function DoneWorkout({ done }: { done: TodayData["today_done"][number] }) {
  const nav = useNav();
  const sport = sportOf(done.sport);
  // Each part isolated on its own: "6.00 ק״מ" mixes directions, and joined into one run the bidi algorithm reorders it.
  const result = [done.meters ? distance(done.meters, done.sport) : null, done.seconds ? duration(done.seconds) : null]
    .filter((x): x is string => Boolean(x));
  return (
    <button type="button" className="inner-box done-workout" onClick={() => nav.openActivity(done.id)}>
      <span className="done-check"><Icon name="check" size={22} stroke={2.6} /></span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="workout-name">{done.name || sport.label}</div>
        <div className="stat-sub">{sport.icon} {sport.label}{result.map((r) => <span key={r}> · <bdi>{r}</bdi></span>)}</div>
      </div>
      <Icon name="chevronLeft" size={20} />
    </button>
  );
}

function NextWorkout({ next }: { next: NonNullable<TodayData["next_workout"]> }) {
  const sport = sportOf(next.sport);
  const day = new Date(`${next.date}T00:00:00`);
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const when = isoOf(day) === isoOf(tomorrow) ? "מחר" : day.toLocaleDateString("he-IL", { weekday: "long" });
  return (
    <div className="inner-box">
      <div className="workout-row">
        <span className="icon-circle lg" style={{ "--tone": "var(--muted)" } as CSSProperties}>{sport.icon}</span>
        <div style={{ minWidth: 0 }}>
          <div className="kicker">האימון הבא · {when}</div>
          <div className="workout-name">{next.name}</div>
          <div className="stat-sub">{sport.label}{next.on_watch ? " · נמצא בשעון" : ""}</div>
        </div>
      </div>
    </div>
  );
}

/** The week in seven circles, as on the goals tab - smaller here, it's a footnote to today. */
function WeekStrip({ week }: { week: WeekDay[] }) {
  const today = todayIso();
  return (
    <div className="week-row home-week" aria-label="השבוע">
      {week.map((d, i) => {
        const isDone = d.done.length > 0;
        const isToday = d.date === today;
        const cls = ["week-circle", isDone && "done", isToday && "today", !isDone && d.planned.length > 0 && "planned"]
          .filter(Boolean).join(" ");
        const names = [...d.done.map((w) => `✓ ${w.name}`), ...d.planned.map((w) => w.name)].join("\n");
        return (
          <div key={d.date} title={names || undefined}>
            <div className={cls}>{isDone ? <Icon name="check" size={15} stroke={2.6} /> : null}</div>
            <div className={`week-day-name${isToday ? " today" : ""}`}>{DAY_NAMES[i]}</div>
          </div>
        );
      })}
    </div>
  );
}

function TodayWorkout({ data }: { data: TodayData }) {
  // Done beats planned: once today's workout is in, the card says so.
  const title = data.today_done.length > 0 || data.workouts.length > 0 ? "האימון של היום" : "אין אימון היום";
  return (
    <section className="card">
      <h2 className="card-title" style={{ marginBottom: 12 }}>{title}</h2>
      <div className="grid" style={{ gap: 10 }}>
        {data.today_done.length > 0
          ? data.today_done.map((d) => <DoneWorkout key={d.id} done={d} />)
          : data.workouts.map((w, i) => <PlannedWorkout key={i} workout={w} />)}
        {data.today_done.length === 0 && data.workouts.length === 0 && (
          data.next_workout
            ? <NextWorkout next={data.next_workout} />
            : <p className="card-caption" style={{ margin: 0 }}>אין אימון מתוכנן. אפשר לבנות אחד עם המאמן בבוט.</p>
        )}
      </div>
      <div className="home-divider" />
      <WeekStrip week={data.week} />
    </section>
  );
}

// --- 3. activity rings ------------------------------------------------------------

type Mode = "day" | "week";

/** The one line under the rings: the nearest goal still open, in words. */
function nudge(rings: ActivityRings, mode: Mode): string {
  const steps = mode === "day" ? rings.day : rings.week;
  const left = [
    { p: steps.steps / steps.step_goal, text: `עוד ${fmt(steps.step_goal - steps.steps)} צעדים ליעד${mode === "week" ? " השבועי" : ""}` },
    {
      p: rings.week.intensity_minutes / rings.week.intensity_goal,
      text: `עוד ${fmt(rings.week.intensity_goal - rings.week.intensity_minutes)} דקות עצימות השבוע`,
    },
    {
      p: rings.week.workouts / rings.week.workouts_goal,
      text: rings.week.workouts_goal - rings.week.workouts === 1
        ? "עוד אימון אחד והשבוע סגור"
        : `עוד ${rings.week.workouts_goal - rings.week.workouts} אימונים והשבוע סגור`,
    },
  ].filter((r) => r.p < 1);
  if (left.length === 0) return "כל הטבעות סגורות. כל הכבוד!";
  return left.reduce((a, b) => (b.p > a.p ? b : a)).text;
}

function ActivityRingsCard({ rings }: { rings: ActivityRings }) {
  const nav = useNav();
  const [mode, setMode] = useState<Mode>("day");
  const steps = mode === "day" ? rings.day : rings.week;
  const rows = [
    { key: "steps", label: mode === "day" ? "צעדים היום" : "צעדים השבוע", value: steps.steps, goal: steps.step_goal },
    { key: "intensity", label: "דקות עצימות השבוע", value: rings.week.intensity_minutes, goal: rings.week.intensity_goal },
    { key: "workouts", label: "אימונים השבוע", value: rings.week.workouts, goal: rings.week.workouts_goal },
  ];
  return (
    <section className="card home-rings" {...tappable(() => nav.openTab("goals", "goals-week"))}>
      <div className="card-head">
        <h2 className="card-title">טבעות פעילות</h2>
        {/* stopPropagation: switching the view is not a tap on the card. */}
        <div className="home-toggle" role="group" aria-label="תצוגה" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          {(["day", "week"] as Mode[]).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>
              {m === "day" ? "היום" : "השבוע"}
            </button>
          ))}
        </div>
      </div>
      <div className="rings-stage">
        <Rings rings={rows.map((r) => ({ key: r.key, label: r.label, progress: r.goal > 0 ? r.value / r.goal : 0 }))} />
        <div className="rings-streak">
          <span className="streak-icon"><Icon name="flame" size={16} stroke={2} /></span>
          <span className="num streak-value">{rings.streak_weeks}</span>
          <span className="streak-label">שבועות<br />ברצף</span>
        </div>
      </div>
      <div className="rings-legend">
        {rows.map((r) => (
          <div key={r.key} className={`legend-row ring-${r.key}`}>
            <span className="legend-dot" />
            <span className="legend-label">{r.label}</span>
            <span className="legend-value num">
              <b>{fmt(r.value)}</b> / {fmt(r.goal)}
            </span>
          </div>
        ))}
      </div>
      <p className="rings-nudge">{nudge(rings, mode)}</p>
    </section>
  );
}

// --- 4. load balance --------------------------------------------------------------

function LoadBalanceCard({ data }: { data: LoadBalanceData | null }) {
  const nav = useNav();
  return (
    <section className="card home-load" {...tappable(() => nav.openTab("workouts", "workouts-form"))}>
      <div className="card-head">
        <h2 className="card-title">מאזן עומס</h2>
        {data && <span className="load-number">Form <span className="num">{data.form > 0 ? "+" : ""}{Math.round(data.form)}</span></span>}
      </div>
      {data ? (
        <>
          <div className={`load-word zone-${data.zone}`}>{data.word}</div>
          <p className="load-advice">{data.advice}</p>
          <LoadGauge data={data} />
        </>
      ) : (
        <p className="card-caption" style={{ margin: "6px 0 0" }}>אין עדיין אימונים עם עומס. אחרי כמה אימונים יופיע כאן כמה הגוף ספג.</p>
      )}
    </section>
  );
}

// --- the screen -------------------------------------------------------------------

export function Today({ name }: { name: string }) {
  const { data } = useApi<TodayData>("/api/today");
  const now = new Date();
  const date = now.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });

  return (
    <>
      <PageHeader title={`${greeting(now.getHours())}${name ? `, ${name}` : ""}`} subtitle={date} />
      <div className="grid home">
        <Readiness data={data} />
        {data ? <TodayWorkout data={data} /> : <div className="skeleton" style={{ height: 200, borderRadius: 22 }} />}
        {data ? <ActivityRingsCard rings={data.activity_rings} /> : <div className="skeleton" style={{ height: 320, borderRadius: 22 }} />}
        {data && <LoadBalanceCard data={data.load_balance} />}
      </div>
    </>
  );
}
