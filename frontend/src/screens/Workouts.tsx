import { useCallback, useEffect, useRef, useState } from "react";
import { Chart, type Palette } from "../charts/Chart";
import {
  fitnessFatigue,
  form,
  hoursBySport,
  yearCalendar,
  type CalendarDay,
  type HoursBucket,
  type LoadDay,
} from "../charts/workoutCharts";
import { HistoryBanner } from "../components/HistoryBanner";
import { RangeSelector } from "../components/RangeSelector";
import { Card, ChartPlaceholder, PageHeader, Stat } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useRange } from "../range";

type Totals = { workouts: number; distance_km: number; moving_hours: number; elevation_m: number };
type Summary = { totals: Totals; previous: Totals; bucket: string; hours_by_sport: HoursBucket[] };
type Load = { days: LoadDay[] };
type Calendar = { start: string; end: string; days: CalendarDay[] };

const fmt = (n: number, digits = 0) =>
  n.toLocaleString("he-IL", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const PREVIOUS: Record<string, string> = { week: "מהשבוע הקודם", month: "מהחודש הקודם", year: "מהשנה הקודמת" };

/** "▲ 12% מהשבוע הקודם" - or nothing when there's no previous period to compare with. */
function change(now: number, before: number, kind: string): string | undefined {
  if (!before) return undefined;
  const pct = Math.round(((now - before) / before) * 100);
  if (pct === 0) return `ללא שינוי ${PREVIOUS[kind]}`;
  return `${pct > 0 ? "▲" : "▼"} ${Math.abs(pct)}% ${PREVIOUS[kind]}`;
}

function Stats({ data, kind }: { data: Summary | null; kind: string }) {
  const t = data?.totals;
  const prev = data?.previous;
  return (
    <div className="grid grid-2">
      <Stat label="אימונים" value={t && fmt(t.workouts)} sub={t && prev ? change(t.workouts, prev.workouts, kind) : undefined} />
      <Stat
        label='מרחק, ק"מ'
        value={t && fmt(t.distance_km, t.distance_km < 100 ? 1 : 0)}
        sub={t && prev ? change(t.distance_km, prev.distance_km, kind) : undefined}
      />
      <Stat
        label="זמן תנועה, שעות"
        value={t && fmt(t.moving_hours, 1)}
        sub={t ? (t.moving_hours >= 24 ? `${fmt(t.moving_hours / 24, 1)} ימים מלאים` : undefined) : undefined}
      />
      <Stat
        label="טיפוס, מטר"
        value={t && fmt(t.elevation_m)}
        sub={t && t.elevation_m >= 2000 ? `${fmt(t.elevation_m / 8849, 1)} אוורסטים` : undefined}
      />
    </div>
  );
}

/** The calendar fits its 53 columns to the card's real width. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(340);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

function CalendarCard() {
  const { data } = useApi<Calendar>("/api/calendar");
  const { ref, width } = useWidth<HTMLDivElement>();
  const build = useCallback(
    (p: Palette) => yearCalendar(p, data!.start, data!.end, data!.days, width),
    [data, width],
  );
  const cell = Math.max(5, Math.floor((width - 24) / 54));
  return (
    <Card title="השנה ביום-יום" caption="כל משבצת היא יום. ככל שהיא כהה יותר, העומס באותו יום גבוה יותר.">
      <div ref={ref}>{data ? <Chart build={build} height={cell * 7 + 34} /> : <ChartPlaceholder height={110} />}</div>
    </Card>
  );
}

export function Workouts() {
  const range = useRange();
  const query = `kind=${range.kind}&offset=${range.offset}`;
  const summary = useApi<Summary>(`/api/workouts?${query}`);
  const load = useApi<Load>(`/api/load?${query}`);

  const hours = summary.data?.hours_by_sport;
  const days = load.data?.days;
  const buildHours = useCallback((p: Palette) => hoursBySport(p, hours ?? [], range.kind), [hours, range.kind]);
  const buildFitness = useCallback((p: Palette) => fitnessFatigue(p, days ?? []), [days]);
  const buildForm = useCallback((p: Palette) => form(p, days ?? []), [days]);

  // The API widens the week's load charts to six weeks (a week is too short for a trend).
  const loadSpan = range.kind === "week" ? " מוצג: 6 השבועות האחרונים." : "";
  const hoursTitle = summary.data?.bucket === "week" ? "שעות בשבוע, לפי ענף" : "שעות ביום, לפי ענף";

  return (
    <>
      <PageHeader kicker="אימונים ועומס" title="אימונים" subtitle="כל מה שהשעון רשם, בטווח שבחרת." />
      <HistoryBanner />
      <RangeSelector />
      <div className="grid">
        <Stats data={summary.data} kind={range.kind} />

        <Card title={hoursTitle} caption="צבע לכל ענף. לחיצה על ענף במקרא מסתירה אותו.">
          {hours ? <Chart build={buildHours} height={230} /> : <ChartPlaceholder height={230} />}
        </Card>

        <Card title="Fitness מול Fatigue" caption={`Fitness נבנה לאט, לאורך כ-6 שבועות. Fatigue הוא השבוע האחרון, וקופץ מהר.${loadSpan}`}>
          {days ? (
            days.length ? <Chart build={buildFitness} height={220} /> : <p className="stat-sub">אין עדיין אימונים עם עומס.</p>
          ) : (
            <ChartPlaceholder height={220} />
          )}
        </Card>

        <Card title="Form" caption={`Fitness פחות Fatigue. מתחת לאפס: סופגים עבודה. אדום: מעבר לקו ה-30-, אזור סיכון.${loadSpan}`}>
          {days ? days.length ? <Chart build={buildForm} height={160} /> : null : <ChartPlaceholder height={160} />}
        </Card>

        <CalendarCard />

        {(summary.error || load.error) && <p className="stat-sub">חלק מהנתונים לא נטענו. נסה לסגור ולפתוח שוב.</p>}
      </div>
    </>
  );
}
