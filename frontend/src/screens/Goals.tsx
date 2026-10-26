import { useCallback, useState } from "react";
import { Chart, type Palette } from "../charts/Chart";
import { clock, raceTrend, simpleTrend, type Point } from "../charts/healthCharts";
import { Icon } from "../components/icons";
import { BACK_TO_BOT, Card, ChartPlaceholder, EmptyState, HeroCard, PageHeader, RETRY } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { scrollToFocus } from "../nav";
import { SPORTS } from "../sports";
import { hapticSelect } from "../telegram";

type Race = { key: string; label: string; days: Point[]; now: number; before: number };
type Day = {
  date: string;
  planned: { name: string; sport: string; on_watch: boolean }[];
  done: { name: string; sport: string; minutes: number }[];
};
type Goals = { races: Race[]; vo2max: Point[]; week: Day[]; plans: { discipline: string; updated: string }[] };

const DAY_NAMES = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"];
const DISCIPLINES: Record<string, string> = { running: "ריצה", cycling: "רכיבה", swimming: "שחייה", strength: "כוח" };
const sportOf = (key: string) => SPORTS.find((s) => s.key === key) ?? SPORTS[SPORTS.length - 1];
const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const FULL_DAY_NAMES = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

/** What a tapped day holds - a phone has no hover, so the circles' tooltips never showed there. */
function DayDetail({ day, index }: { day: Day; index: number }) {
  const empty = day.done.length === 0 && day.planned.length === 0;
  return (
    <div className="week-detail" role="status">
      <strong>יום {FULL_DAY_NAMES[index]}</strong>
      {empty ? (
        <span className="stat-sub"> · בלי אימונים</span>
      ) : (
        <ul>
          {day.done.map((w, i) => (
            <li key={`d${i}`}>✓ {sportOf(w.sport).icon} {w.name} · <span className="num">{w.minutes}</span> דק׳</li>
          ))}
          {day.planned.map((w, i) => (
            <li key={`p${i}`} className="stat-sub">מתוכנן: {sportOf(w.sport).icon} {w.name}{w.on_watch ? " · בשעון" : ""}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** One circle per day, as in the mockup: black with a check when done, an orange ring for today. */
function WeekCard({ week }: { week: Day[] }) {
  const today = todayIso();
  const [picked, setPicked] = useState<number | null>(null);
  const planned = week.reduce((a, d) => a + d.planned.length, 0);
  const done = week.reduce((a, d) => a + d.done.length, 0);
  return (
    <Card id="goals-week" title="השבוע הנוכחי" caption="ראשון עד שבת. שחור: בוצע. מקווקו: מתוכנן. לחיצה על יום מפרטת אותו.">
      <div className="week-row">
        {week.map((d, i) => {
          const isDone = d.done.length > 0;
          const isToday = d.date === today;
          const cls = ["week-circle", isDone && "done", isToday && "today", !isDone && d.planned.length > 0 && "planned"].filter(Boolean).join(" ");
          const sports = (isDone ? d.done : d.planned).map((w) => sportOf(w.sport).icon);
          const names = [...d.done.map((w) => `✓ ${w.name} (${w.minutes} דק׳)`), ...d.planned.map((w) => `${w.name}${w.on_watch ? " ⌚" : ""}`)].join("\n");
          return (
            <button
              key={d.date}
              title={names || undefined}
              aria-pressed={picked === i}
              aria-label={`יום ${FULL_DAY_NAMES[i]}`}
              onClick={() => {
                hapticSelect();
                setPicked(picked === i ? null : i);
              }}
            >
              <div className={cls}>{isDone ? <Icon name="check" size={18} stroke={2.6} /> : null}</div>
              <div className={`week-day-name${isToday ? " today" : ""}`}>{DAY_NAMES[i]}</div>
              <div className="week-sports">{sports.slice(0, 2).join("")}</div>
            </button>
          );
        })}
      </div>
      {picked !== null && week[picked] && <DayDetail day={week[picked]} index={picked} />}
      <p className="stat-sub" style={{ marginTop: 10 }}>
        {planned > 0 ? <>תוכננו <span className="num">{planned}</span>, בוצעו <span className="num">{done}</span></> : <>בוצעו <span className="num">{done}</span> אימונים. אין אימונים מתוכננים השבוע.</>}
      </p>
    </Card>
  );
}

/** The dark card: the active plans and how this week is going against them. */
function PlanHero({ data }: { data: Goals }) {
  // Matched by count per day, like the weekly report: a planned day counts once something was done on it.
  const planned = data.week.reduce((a, d) => a + d.planned.length, 0);
  const met = data.week.reduce((a, d) => a + Math.min(d.done.length, d.planned.length), 0);
  const done = data.week.reduce((a, d) => a + d.done.length, 0);
  const minutes = data.week.reduce((a, d) => a + d.done.reduce((m, w) => m + w.minutes, 0), 0);
  const latest = data.plans.map((p) => p.updated).sort().at(-1);
  return (
    <HeroCard
      kicker={data.plans.length > 1 ? "תוכניות פעילות" : "תוכנית נוכחית"}
      title={data.plans.map((p) => DISCIPLINES[p.discipline] ?? p.discipline).join(" · ")}
      sub={latest ? <>עודכנה {new Date(`${latest}T00:00:00`).toLocaleDateString("he-IL", { day: "numeric", month: "short", year: "numeric" })}</> : undefined}
      progress={planned > 0 ? met / planned : undefined}
      onClick={() => scrollToFocus("goals-week")}
      stats={[
        planned > 0
          ? { value: <span className="num">{met} / {planned}</span>, label: "מהתוכנית בוצעו" }
          : { value: <span className="num">{done}</span>, label: "אימונים השבוע" },
        { value: <span className="num">{(minutes / 60).toFixed(1)}</span>, label: "שעות השבוע" },
        { value: <span className="num">{data.plans.length}</span>, label: "תוכניות" },
      ]}
    />
  );
}

function RaceCard({ race }: { race: Race }) {
  const build = useCallback((p: Palette) => raceTrend(p, race.days, p.sport.running), [race]);
  const diff = race.now - race.before;
  return (
    <section className="card">
      <div className="metric-head">
        <div>
          <h2 className="card-title">{race.label}</h2>
          <p className="card-caption">
            {Math.abs(diff) < 1 ? "ללא שינוי מלפני 3 חודשים" : (
              <span style={{ color: diff < 0 ? "var(--sport-swimming)" : "var(--zone-5)" }}>
                {diff < 0 ? "▲ מהיר ב-" : "▼ איטי ב-"}<span className="num">{clock(Math.abs(diff))}</span> מלפני 3 חודשים
              </span>
            )}
          </p>
        </div>
        <div className="metric-value">
          <div className="kicker">תחזית היום</div>
          <div className="stat-value num">{clock(race.now)}</div>
        </div>
      </div>
      <Chart build={build} height={110} />
    </section>
  );
}

export function Goals() {
  const { data, error } = useApi<Goals>("/api/goals");
  const buildVo2 = useCallback((p: Palette) => simpleTrend(p, data?.vo2max ?? [], p.sport.other), [data]);
  const vo2 = data?.vo2max.at(-1)?.[1];

  return (
    <>
      <PageHeader title="יעדים ותוכניות" subtitle="מעקב אחר התקדמות וביצוע תוכניות." />
      <div className="grid">
        {error && <EmptyState card icon="alert" title="הנתונים לא נטענו" text="בדרך כלל זה חולף תוך רגע." action={RETRY} />}
        {!data && !error && <div className="skeleton" style={{ height: 220, borderRadius: 24 }} />}
        {data && data.plans.length > 0 && <PlanHero data={data} />}
        {data && data.plans.length === 0 && (
          <EmptyState
            card icon="target" title="אין עדיין תוכנית פעילה"
            text="ספר למאמן בבוט על היעד שלך, והוא יבנה תוכנית שתופיע כאן."
            action={BACK_TO_BOT}
          />
        )}
        {data ? <WeekCard week={data.week} /> : <Card title="השבוע הנוכחי" caption="ראשון עד שבת."><ChartPlaceholder height={90} /></Card>}

        {data && data.races.length > 0 && (
          <>
            <h2 className="section-title">תחזיות זמנים</h2>
            <p className="section-caption">מה Garmin חושבת שתעשה במרוץ היום, לאורך השנה. למעלה = מהיר יותר.</p>
            {data.races.map((race) => <RaceCard key={race.key} race={race} />)}
          </>
        )}

        {data && data.vo2max.length > 0 && (
          <section className="card">
            <div className="metric-head">
              <div>
                <h2 className="card-title">VO2max</h2>
                <p className="card-caption">כמות החמצן המקסימלית שהגוף מנצל. Garmin מעדכנת אותו אחרי אימונים מתאימים.</p>
              </div>
              <div className="metric-value">
                <div className="kicker">עכשיו</div>
                <div className="stat-value num">{vo2}</div>
              </div>
            </div>
            <Chart build={buildVo2} height={110} />
          </section>
        )}

        <Card title="התקדמות ליעד" caption="יעד עם תאריך, והקו הנדרש כדי להגיע אליו." soon="אחרי התוכנית ליעד במאמן" />
      </div>
    </>
  );
}
