import type { CSSProperties } from "react";
import { Card, EmptyState, HeroCard, PageHeader, RETRY, Stat } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useNav } from "../nav";
import { useRange } from "../range";
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
  const shown = SPORTS.filter((s) => year.hours_by_sport[s.key] > 0);
  const nav = useNav();
  const range = useRange();
  return (
    <>
      <HeroCard
        kicker="השנה שלי · 365 הימים האחרונים"
        title={year.top_sport ? <>{top.icon} {top.label} מובילה</> : "השנה שלך באימונים"}
        onClick={() => {
          // The same year, broken down: the workouts tab in its year view.
          range.setKind("year");
          nav.openTab("workouts");
        }}
        sub={year.best_month ? <>החודש החזק: {monthName(year.best_month)} · <span className="num">{year.best_month_hours}</span> ש׳</> : undefined}
        stats={[
          { value: <span className="num">{n(year.workouts)}</span>, label: "אימונים" },
          { value: <span className="num">{n(year.km)}</span>, label: "ק״מ" },
          { value: <span className="num">{n(year.hours)}</span>, label: "שעות" },
        ]}
      >
        {total > 0 && (
          <div className="sport-split" dir="ltr" aria-hidden="true" style={{ marginTop: 18 }}>
            {shown.map((s) => <span key={s.key} style={{ flex: year.hours_by_sport[s.key], background: s.color }} />)}
          </div>
        )}
      </HeroCard>
      {total > 0 && (
        <Card title="לפי ענף" caption="איך מתחלקות שעות האימון של השנה.">
          <ul className="list">
            {shown.map((s) => (
              <li key={s.key}>
                <span className="row-start">
                  <span className="icon-circle" style={{ "--tone": s.color } as CSSProperties}>{s.icon}</span>
                  {s.label}
                </span>
                <span><strong className="num">{Math.round((100 * year.hours_by_sport[s.key]) / total)}%</strong> <span className="stat-sub num">{n(Math.round(year.hours_by_sport[s.key]))} ש׳</span></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {(year.farthest || year.longest) && (
        <div className="grid grid-2">
          {year.farthest && <Stat icon="route" tone="var(--orange)" label="הכי רחוק" value={String(year.farthest.km)} unit="ק״מ" sub={date(year.farthest.date)} />}
          {year.longest && <Stat icon="clock" tone="var(--sport-running)" label="הכי ארוך" value={String(year.longest.minutes)} unit="דק׳" sub={date(year.longest.date)} />}
        </div>
      )}
    </>
  );
}

export function Records() {
  const { data, error } = useApi<Records>("/api/records");
  const nav = useNav();
  return (
    <>
      <PageHeader title="שיאים" subtitle="הטובים ביותר שלך, בכל ענף." />
      <div className="grid">
        {error && <EmptyState card icon="alert" title="הנתונים לא נטענו" text="בדרך כלל זה חולף תוך רגע." action={RETRY} />}
        {!data && !error && <div className="skeleton" style={{ height: 260, borderRadius: 24 }} />}
        {data && <MyYear year={data.year} />}

        {data && (
          <Card title="שיאים אישיים" caption="השיאים ש-Garmin רשמה, בכל הזמנים.">
            {data.personal_records.length ? (
              <ul className="list">
                {data.personal_records.map((r) => (
                  <li key={r.key}>
                    <span className="row-start">
                      <span className="icon-circle" style={{ "--tone": sportOf(r.sport).color } as CSSProperties}>{sportOf(r.sport).icon}</span>
                      {r.label}
                    </span>
                    <span><strong className="num">{r.value}</strong> <span className="stat-sub">{date(r.date)}</span></span>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon="medal" text="עוד אין שיאים. Garmin רושמת שיא אחרי ריצה, רכיבה או שחייה מהירות מכל מה שהיה." />
            )}
          </Card>
        )}

        {data && data.next.length > 0 && (
          // The streak at the bottom counts weeks; this week's card shows how the current one is going.
          <Card title="הבא בתור" caption="אבני הדרך הבאות, וכמה נשאר." onClick={() => nav.openTab("goals", "goals-week")}>
            {data.next.map((m) => (
              <div key={m.text} style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>{m.icon} {m.text}</span>
                  <span className="stat-sub num">{n(m.progress)}/{n(m.target)}</span>
                </div>
                <div className="progress"><span style={{ width: `${Math.min(100, (100 * m.progress) / m.target)}%` }} /></div>
              </div>
            ))}
            <p className="stat-sub" style={{ marginBottom: 0 }}>🔥 רצף נוכחי: <span className="num">{data.streak_weeks}</span> שבועות עם {data.streak_rule}+ אימונים</p>
          </Card>
        )}

        {data && data.reached.length > 0 && (
          <Card title="קיר ההישגים" caption="כל מה שכבר הושג, מהחדש לישן.">
            <ul className="list">
              {data.reached.map((r) => (
                <li key={`${r.text}${r.date}`}><span className="row-start"><span className="icon-circle" style={{ "--tone": "var(--orange)" } as CSSProperties}>{r.icon}</span>{r.text}</span><span className="stat-sub">{date(r.date)}</span></li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  );
}
