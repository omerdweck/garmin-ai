import { Card, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { SPORTS } from "../sports";

type Records = {
  personal_records: { key: string; sport: string; label: string; value: string; date: string | null }[];
  reached: { icon: string; text: string; date: string }[];
  next: { icon: string; text: string; progress: number; target: number }[];
  streak_weeks: number;
  streak_rule: number;
  year: {
    workouts: number; km: number; hours: number; top_sport: string | null;
    hours_by_sport: Record<string, number>; best_month: string | null; best_month_hours: number;
    longest: { name: string; minutes: number; date: string } | null;
    farthest: { name: string; km: number; date: string } | null;
  };
};

const date = (iso: string | null) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("he-IL", { day: "numeric", month: "numeric", year: "2-digit" }) : "");
const monthName = (ym: string) => new Date(`${ym}-01T00:00:00`).toLocaleDateString("he-IL", { month: "long", year: "numeric" });
const sportOf = (key: string | null) => SPORTS.find((s) => s.key === key) ?? SPORTS[SPORTS.length - 1];
const n = (v: number) => v.toLocaleString("he-IL");

function MyYear({ year }: { year: Records["year"] }) {
  const total = Object.values(year.hours_by_sport).reduce((a, b) => a + b, 0);
  const top = sportOf(year.top_sport);
  return (
    <section className="card">
      <div className="kicker">השנה שלי · 365 הימים האחרונים</div>
      <div className="grid grid-2" style={{ marginTop: 10, gap: 8 }}>
        <div><div className="stat-value num">{n(year.workouts)}</div><div className="stat-sub">אימונים</div></div>
        <div><div className="stat-value num">{n(year.km)}</div><div className="stat-sub">ק״מ</div></div>
        <div><div className="stat-value num">{n(year.hours)}</div><div className="stat-sub">שעות</div></div>
        <div><div className="stat-value">{year.top_sport ? top.icon : "—"}</div><div className="stat-sub">הענף המוביל: {year.top_sport ? top.label : "—"}</div></div>
      </div>
      {total > 0 && (
        <>
          <div className="split-bar" dir="ltr" aria-hidden="true">
            {SPORTS.filter((s) => year.hours_by_sport[s.key] > 0).map((s) => (
              <span key={s.key} style={{ flex: year.hours_by_sport[s.key], background: s.color }} />
            ))}
          </div>
          <div className="chart-notes">
            {SPORTS.filter((s) => year.hours_by_sport[s.key] > 0).map((s) => (
              <span key={s.key}><i className="swatch" style={{ background: s.color }} />{s.label} <span className="num">{Math.round((100 * year.hours_by_sport[s.key]) / total)}%</span></span>
            ))}
          </div>
        </>
      )}
      <ul className="list" style={{ marginTop: 10 }}>
        {year.best_month && <li><span>📅 החודש החזק</span><span>{monthName(year.best_month)} · <span className="num">{year.best_month_hours}</span> ש׳</span></li>}
        {year.farthest && <li><span>📏 הכי רחוק</span><span><span className="num">{year.farthest.km}</span> ק״מ · {date(year.farthest.date)}</span></li>}
        {year.longest && <li><span>⏱ הכי ארוך</span><span><span className="num">{year.longest.minutes}</span> דק׳ · {date(year.longest.date)}</span></li>}
      </ul>
    </section>
  );
}

export function Records() {
  const { data, error } = useApi<Records>("/api/records");
  return (
    <>
      <PageHeader kicker="שיאים והישגים" title="שיאים" subtitle="הטובים ביותר שלך, בכל ענף." />
      <div className="grid">
        {error && <p className="stat-sub">הנתונים לא נטענו. נסה לסגור ולפתוח שוב.</p>}
        {!data && !error && <div className="skeleton" style={{ height: 260 }} />}
        {data && <MyYear year={data.year} />}

        {data && (
          <Card title="שיאים אישיים" caption="השיאים ש-Garmin רשמה, בכל הזמנים.">
            {data.personal_records.length ? (
              <ul className="list">
                {data.personal_records.map((r) => (
                  <li key={r.key}>
                    <span>{sportOf(r.sport).icon} {r.label}</span>
                    <span><strong className="num">{r.value}</strong> <span className="stat-sub">{date(r.date)}</span></span>
                  </li>
                ))}
              </ul>
            ) : <p className="stat-sub">עוד אין שיאים רשומים.</p>}
          </Card>
        )}

        {data && data.next.length > 0 && (
          <Card title="הבא בתור" caption="אבני הדרך הבאות, וכמה נשאר.">
            {data.next.map((m) => (
              <div key={m.text} style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>{m.icon} {m.text}</span>
                  <span className="stat-sub num">{n(m.progress)}/{n(m.target)}</span>
                </div>
                <div className="progress"><span style={{ width: `${Math.min(100, (100 * m.progress) / m.target)}%` }} /></div>
              </div>
            ))}
            <p className="stat-sub">🔥 רצף נוכחי: <span className="num">{data.streak_weeks}</span> שבועות עם {data.streak_rule}+ אימונים</p>
          </Card>
        )}

        {data && data.reached.length > 0 && (
          <Card title="קיר ההישגים" caption="כל מה שכבר הושג, מהחדש לישן.">
            <ul className="list">
              {data.reached.map((r) => (
                <li key={`${r.text}${r.date}`}><span>{r.icon} {r.text}</span><span className="stat-sub">{date(r.date)}</span></li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  );
}
