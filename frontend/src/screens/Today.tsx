import type { LoadDay } from "../charts/workoutCharts";
import { Card, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { SPORTS } from "../sports";

type Ring = {
  key: string; label: string; value: number | null; as_of: string | null;
  fill: number; state: "good" | "normal" | "low" | "unknown";
  band: { mean: number; low: number; high: number } | null;
};
type Block = { kind: string; seconds: number; label?: string };
type TodayData = {
  rings: Ring[];
  readiness_score: number | null;
  alert: { text: string; since: string } | null;
  workouts: { name: string; sport: string; on_watch: boolean; blocks: Block[] }[];
  streak_weeks: number;
  streak_rule: number;
};

const STATE_COLOR = { good: "var(--sport-swimming)", normal: "var(--accent)", low: "var(--zone-5)", unknown: "var(--skeleton)" };
const STATE_WORD = { good: "מעל הרגיל", normal: "בטווח הרגיל", low: "מתחת לרגיל", unknown: "אין נתון" };

function greeting(hour: number): string {
  if (hour < 5) return "לילה טוב";
  if (hour < 12) return "בוקר טוב";
  if (hour < 17) return "צהריים טובים";
  if (hour < 21) return "ערב טוב";
  return "לילה טוב";
}

const isToday = (iso: string | null) => {
  if (!iso) return false;
  const d = new Date();
  return iso === `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

function ReadinessRing({ ring }: { ring: Ring }) {
  const circumference = 113;
  const color = STATE_COLOR[ring.state];
  return (
    <div style={{ textAlign: "center", flex: 1 }}>
      <svg viewBox="0 0 44 44" width="66" height="66" role="img" aria-label={`${ring.label}: ${ring.value ?? "אין נתון"}`}>
        <circle cx="22" cy="22" r="18" fill="none" stroke="var(--skeleton)" strokeWidth="5" />
        <circle
          cx="22" cy="22" r="18" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={circumference * (1 - ring.fill)}
          transform="rotate(-90 22 22)" style={{ transition: "stroke-dashoffset 0.8s ease" }}
        />
        <text x="22" y="26" textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--text)" style={{ fontFamily: "var(--font-body)" }}>
          {ring.value ?? "—"}
        </text>
      </svg>
      <div style={{ fontSize: 12, fontWeight: 500 }}>{ring.label}</div>
      <div className="stat-sub" style={{ fontSize: 11, color }}>{STATE_WORD[ring.state]}</div>
      {ring.value !== null && !isToday(ring.as_of) && <div className="stat-sub" style={{ fontSize: 10 }}>מאתמול</div>}
    </div>
  );
}

// Intensity strip: taller and warmer for harder blocks.
const BLOCK_STYLE: Record<string, { h: number; color: string }> = {
  warmup: { h: 40, color: "var(--zone-2)" },
  interval: { h: 100, color: "var(--zone-5)" },
  recovery: { h: 30, color: "var(--zone-1)" },
  rest: { h: 18, color: "var(--zone-1)" },
  cooldown: { h: 40, color: "var(--zone-2)" },
  other: { h: 55, color: "var(--zone-3)" },
};

function WorkoutCard({ workout }: { workout: TodayData["workouts"][number] }) {
  const sport = SPORTS.find((s) => s.key === workout.sport) ?? SPORTS[SPORTS.length - 1];
  const total = workout.blocks.reduce((a, b) => a + b.seconds, 0);
  return (
    <section className="card">
      <div className="kicker">האימון של היום</div>
      <h2 className="card-title">{sport.icon} {workout.name}</h2>
      <p className="card-caption">
        {workout.on_watch ? "⌚ נמצא בשעון" : "שמור בבוט, לא בשעון"}
        {total > 0 && <> · כ-<span className="num">{Math.round(total / 60)}</span> דקות</>}
      </p>
      {total > 0 && (
        <div className="blocks" dir="ltr" aria-hidden="true">
          {workout.blocks.map((b, i) => {
            const style = BLOCK_STYLE[b.kind] ?? BLOCK_STYLE.other;
            return <span key={i} style={{ flex: b.seconds, height: `${style.h}%`, background: style.color }} />;
          })}
        </div>
      )}
    </section>
  );
}

function formWords(value: number): { text: string; color: string } {
  if (value >= 5) return { text: "רענן. גוף מוכן לאימון קשה", color: "var(--sport-swimming)" };
  if (value >= -10) return { text: "מאוזן", color: "var(--text)" };
  if (value >= -30) return { text: "סופג עבודה. אזור האימון הפרודוקטיבי", color: "var(--accent)" };
  return { text: "עומס גבוה. שווה יום קל", color: "var(--zone-5)" };
}

function FormAndStreak({ streak, rule }: { streak?: number; rule?: number }) {
  const { data } = useApi<{ days: LoadDay[] }>("/api/load?kind=week&offset=0");
  const today = data?.days.at(-1);
  const words = today ? formWords(today.form) : null;
  return (
    <div className="grid grid-2">
      <section className="card">
        <div className="kicker">Form היום</div>
        {today && words ? (
          <>
            <div className="stat-value num" style={{ color: words.color }}>{today.form > 0 ? "+" : ""}{today.form.toFixed(0)}</div>
            <div className="stat-sub">{words.text}</div>
          </>
        ) : data ? <div className="stat-sub">אין עדיין אימונים עם עומס.</div> : <div className="skeleton" style={{ height: 32, width: "50%", marginTop: 8 }} />}
      </section>
      <section className="card">
        <div className="kicker">רצף</div>
        {streak === undefined ? <div className="skeleton" style={{ height: 32, width: "50%", marginTop: 8 }} /> : (
          <>
            <div className="stat-value num">{streak > 0 ? `🔥 ${streak}` : "0"}</div>
            <div className="stat-sub">שבועות ברצף עם {rule}+ אימונים</div>
          </>
        )}
      </section>
    </div>
  );
}

export function Today({ name }: { name: string }) {
  const { data } = useApi<TodayData>("/api/today");
  const now = new Date();
  const date = now.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });

  return (
    <>
      <PageHeader kicker={date} title={`${greeting(now.getHours())}${name ? `, ${name}` : ""}`} />
      <div className="grid">
        {data?.alert && (
          <section className="card alert" role="status">
            <div className="kicker">⚠️ התראה פתוחה</div>
            <div style={{ fontWeight: 500, marginTop: 4 }}>{data.alert.text}</div>
            <div className="stat-sub">
              מאז {new Date(`${data.alert.since}T00:00:00`).toLocaleDateString("he-IL", { day: "numeric", month: "numeric" })} · פרטים בלשונית בריאות
            </div>
          </section>
        )}

        <Card
          kicker={data?.readiness_score != null ? `מוכנות להיום · Garmin: ${data.readiness_score}/100` : "מוכנות להיום"}
          caption="HRV ודופק מנוחה נמדדים מול הנורמה האישית שלך, Body Battery ושינה בסולם 0–100."
        >
          {data ? (
            <div style={{ display: "flex", gap: 4 }}>{data.rings.map((r) => <ReadinessRing key={r.key} ring={r} />)}</div>
          ) : (
            <div className="skeleton" style={{ height: 110 }} />
          )}
        </Card>

        {data?.workouts.map((w, i) => <WorkoutCard key={i} workout={w} />)}
        {data && data.workouts.length === 0 && (
          <Card kicker="האימון של היום" caption="אין אימון מתוכנן להיום. אפשר לבנות אחד עם המאמן בבוט." />
        )}

        <FormAndStreak streak={data?.streak_weeks} rule={data?.streak_rule} />
      </div>
    </>
  );
}
