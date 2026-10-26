import { useCallback } from "react";
import { Chart, type Palette } from "../charts/Chart";
import { clock, raceTrend, simpleTrend, type Point } from "../charts/healthCharts";
import { Card, ChartPlaceholder, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { SPORTS } from "../sports";

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

function WeekCard({ week }: { week: Day[] }) {
  const today = todayIso();
  const planned = week.reduce((a, d) => a + d.planned.length, 0);
  const done = week.reduce((a, d) => a + d.done.length, 0);
  return (
    <Card title="השבוע" caption="ראשון עד שבת. מסגרת מקווקוות: מתוכנן. צבע מלא: בוצע.">
      <div className="week-grid">
        {week.map((d, i) => (
          <div key={d.date} className={`week-day${d.date === today ? " today" : ""}`}>
            <div style={{ fontWeight: 500 }}>{DAY_NAMES[i]}</div>
            <div className="stat-sub num" style={{ fontSize: 10 }}>{Number(d.date.slice(8))}</div>
            {d.planned.map((w, j) => (
              <span key={`p${j}`} className="chip planned" style={{ color: sportOf(w.sport).color }} title={w.name}>
                {w.on_watch ? "⌚" : ""}{sportOf(w.sport).icon}
              </span>
            ))}
            {d.done.map((w, j) => (
              <span key={`d${j}`} className="chip done" style={{ background: sportOf(w.sport).color }} title={w.name}>
                {sportOf(w.sport).icon} <span className="num">{w.minutes}</span>
              </span>
            ))}
          </div>
        ))}
      </div>
      <p className="stat-sub" style={{ marginTop: 10 }}>
        {planned > 0 ? <>תוכננו <span className="num">{planned}</span>, בוצעו <span className="num">{done}</span></> : <>בוצעו <span className="num">{done}</span> אימונים. אין אימונים מתוכננים השבוע.</>}
      </p>
    </Card>
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
      <PageHeader kicker="יעדים ותוכנית" title="יעדים" subtitle="לאן אתה הולך, ואיך אתה מתקדם לשם." />
      <div className="grid">
        {error && <p className="stat-sub">הנתונים לא נטענו. נסה לסגור ולפתוח שוב.</p>}
        {data ? <WeekCard week={data.week} /> : <Card title="השבוע" caption="ראשון עד שבת."><ChartPlaceholder height={90} /></Card>}

        {data && data.plans.length > 0 && (
          <Card title="תוכניות פעילות" caption="תוכניות שאישרת עם המאמן.">
            <ul className="list">
              {data.plans.map((plan) => (
                <li key={plan.discipline}>
                  <span>{sportOf(plan.discipline).icon} {DISCIPLINES[plan.discipline] ?? plan.discipline}</span>
                  <span className="stat-sub">עודכנה {new Date(`${plan.updated}T00:00:00`).toLocaleDateString("he-IL")}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {data && data.races.length > 0 && (
          <>
            <h2 className="card-title" style={{ margin: "12px 4px 0" }}>תחזיות זמנים</h2>
            <p className="card-caption" style={{ margin: "0 4px" }}>מה Garmin חושבת שתעשה במרוץ היום, לאורך השנה. למעלה = מהיר יותר.</p>
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
