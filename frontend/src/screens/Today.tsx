import type { CSSProperties } from "react";
import type { LoadDay } from "../charts/workoutCharts";
import { Icon } from "../components/icons";
import { Card, HeroCard, PageHeader, Stat } from "../components/ui";
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

const STATE_COLOR = { good: "#4fd1a1", normal: "#f5f3ef", low: "#ff8a7a", unknown: "rgba(245,243,239,0.5)" };
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

function readinessWords(score: number): string {
  if (score >= 75) return "מוכן לאימון קשה";
  if (score >= 50) return "מוכנות טובה";
  if (score >= 25) return "יום מתון";
  return "יום התאוששות";
}

/** The dark card: Garmin's readiness score, and each of the four signals under it. */
function Readiness({ data }: { data: TodayData | null }) {
  if (!data) return <div className="skeleton" style={{ height: 230, borderRadius: 24 }} />;
  const score = data.readiness_score;
  return (
    <HeroCard
      kicker="מוכנות להיום"
      title={score != null ? readinessWords(score) : "איך הגוף שלך היום"}
      sub={score != null ? <>ציון המוכנות של Garmin: <span className="num">{score}/100</span></> : "ארבעת המדדים מול הנורמה האישית שלך"}
      progress={score != null ? score / 100 : undefined}
      stats={data.rings.map((r) => ({
        value: <span className="num">{r.value ?? "—"}</span>,
        label: r.label + (r.value !== null && !isToday(r.as_of) ? " · אתמול" : ""),
        state: { text: STATE_WORD[r.state], color: STATE_COLOR[r.state] },
      }))}
    />
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
      <h2 className="card-title" style={{ marginBottom: 12 }}>האימון של היום</h2>
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
    </section>
  );
}

function formWords(value: number): { text: string; color: string } {
  if (value >= 5) return { text: "רענן, מוכן לאימון קשה", color: "var(--good)" };
  if (value >= -10) return { text: "מאוזן", color: "var(--muted)" };
  if (value >= -30) return { text: "סופג עבודה, אזור פרודוקטיבי", color: "var(--accent)" };
  return { text: "עומס גבוה, שווה יום קל", color: "var(--bad)" };
}

function FormAndStreak({ streak, rule }: { streak?: number; rule?: number }) {
  const { data } = useApi<{ days: LoadDay[] }>("/api/load?kind=week&offset=0");
  const today = data?.days.at(-1);
  const words = today ? formWords(today.form) : null;
  return (
    <div className="grid grid-2">
      <Stat
        icon="gauge" tone="var(--sport-running)" label="Form היום"
        value={today ? `${today.form > 0 ? "+" : ""}${today.form.toFixed(0)}` : data ? "—" : undefined}
        sub={words ? words.text : data ? "אין עדיין אימונים עם עומס" : undefined}
      />
      <Stat
        icon="flame" tone="var(--orange)" label="שבועות ברצף"
        value={streak === undefined ? undefined : String(streak)}
        sub={rule !== undefined ? `עם ${rule}+ אימונים בשבוע` : undefined}
      />
    </div>
  );
}

export function Today({ name }: { name: string }) {
  const { data } = useApi<TodayData>("/api/today");
  const now = new Date();
  const date = now.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });

  return (
    <>
      <PageHeader title={`${greeting(now.getHours())}${name ? `, ${name}` : ""}`} subtitle={date} />
      <div className="grid">
        {data?.alert && (
          <section className="card alert" role="status">
            <div className="alert-row">
              <span className="icon-circle" style={{ "--tone": "var(--zone-5)" } as CSSProperties}><Icon name="alert" size={20} /></span>
              <div>
                <div style={{ fontWeight: 700 }}>{data.alert.text}</div>
                <div className="stat-sub">
                  מאז {new Date(`${data.alert.since}T00:00:00`).toLocaleDateString("he-IL", { day: "numeric", month: "numeric" })} · פרטים בלשונית בריאות
                </div>
              </div>
            </div>
          </section>
        )}

        <Readiness data={data} />

        {data?.workouts.map((w, i) => <WorkoutCard key={i} workout={w} />)}
        {data && data.workouts.length === 0 && (
          <Card title="האימון של היום" caption="אין אימון מתוכנן להיום. אפשר לבנות אחד עם המאמן בבוט." />
        )}

        <FormAndStreak streak={data?.streak_weeks} rule={data?.streak_rule} />
      </div>
    </>
  );
}
