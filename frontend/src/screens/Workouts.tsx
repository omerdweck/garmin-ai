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
import { ActivityList } from "../components/ActivityList";
import { HistoryBanner } from "../components/HistoryBanner";
import { RangeSelector } from "../components/RangeSelector";
import { Card, ChartPlaceholder, EmptyState, PageHeader, RETRY, Stat, type Delta } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { scrollToFocus } from "../nav";
import { useRange } from "../range";

type Totals = { workouts: number; distance_km: number; moving_hours: number; elevation_m: number };
type Summary = { totals: Totals; previous: Totals; bucket: string; hours_by_sport: HoursBucket[] };
type Load = { days: LoadDay[] };
type Calendar = { start: string; end: string; days: CalendarDay[] };

const fmt = (n: number, digits = 0) =>
  n.toLocaleString("he-IL", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const PREVIOUS: Record<string, string> = { week: "מהשבוע הקודם", month: "מהחודש הקודם", year: "מהשנה הקודמת" };

/** "↑ 12% מהשבוע הקודם" - or nothing when there's no previous period to compare with. */
function change(now: number, before: number, kind: string): Delta | undefined {
  if (!before) return undefined;
  const pct = Math.round(((now - before) / before) * 100);
  if (pct === 0) return { text: `ללא שינוי ${PREVIOUS[kind]}`, dir: "flat" };
  return { text: `${Math.abs(pct)}% ${PREVIOUS[kind]}`, dir: pct > 0 ? "up" : "down" };
}

// A tile scrolls to the card that breaks its number down - same tab, so
// no nav.openTab(); just the scroll and the flash it would have done.
const jump = (id: string) => () => scrollToFocus(id);

function Stats({ data, kind }: { data: Summary | null; kind: string }) {
  const t = data?.totals;
  const prev = data?.previous;
  return (
    <div className="grid grid-2">
      <Stat icon="bars" tone="var(--text)" label="אימונים" value={t && fmt(t.workouts)} onClick={jump("workouts-list")}
        delta={t && prev ? change(t.workouts, prev.workouts, kind) : undefined} />
      <Stat icon="route" tone="var(--orange)" label="מרחק" unit='ק"מ' onClick={jump("workouts-list")}
        value={t && fmt(t.distance_km, t.distance_km < 100 ? 1 : 0)}
        delta={t && prev ? change(t.distance_km, prev.distance_km, kind) : undefined} />
      <Stat icon="clock" tone="var(--sport-running)" label="זמן תנועה" unit="ש׳" onClick={jump("workouts-hours")}
        value={t && fmt(t.moving_hours, 1)}
        delta={t && prev ? change(t.moving_hours, prev.moving_hours, kind) : undefined}
        sub={t && t.moving_hours >= 24 ? `${fmt(t.moving_hours / 24, 1)} ימים מלאים` : undefined} />
      <Stat icon="mountain" tone="var(--sport-swimming)" label="טיפוס" unit="מ׳"
        value={t && fmt(t.elevation_m)}
        sub={t && t.elevation_m >= 2000 ? `${fmt(t.elevation_m / 8849, 1)} אוורסטים` : undefined} />
    </div>
  );
}

function NoLoad() {
  return <EmptyState icon="pulse" text="עוד אין אימונים עם עומס. אימון עם דופק מהשעון יתחיל למלא את הגרף." />;
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
      <PageHeader title="אימונים" subtitle="כל מה שחשוב, בעמוד אחד." />
      <HistoryBanner />
      <RangeSelector />
      <div className="grid">
        {(summary.error || load.error) && (
          <EmptyState card icon="alert" title="חלק מהנתונים לא נטענו" text="בדרך כלל זה חולף תוך רגע." action={RETRY} />
        )}
        <Stats data={summary.data} kind={range.kind} />

        <Card id="workouts-hours" title={hoursTitle} caption="צבע לכל ענף. לחיצה על ענף במקרא מסתירה אותו.">
          {!hours ? (
            <ChartPlaceholder height={230} />
          ) : summary.data?.totals.workouts ? (
            <Chart build={buildHours} height={230} />
          ) : (
            <EmptyState icon="calendar" text="אין אימונים בתקופה הזו. החצים למעלה עוברים לתקופה קודמת." />
          )}
        </Card>

        <Card title="Fitness מול Fatigue" caption={`Fitness נבנה לאט, לאורך כ-6 שבועות. Fatigue הוא השבוע האחרון, וקופץ מהר.${loadSpan}`}>
          {days ? (
            days.length ? <Chart build={buildFitness} height={220} /> : <NoLoad />
          ) : (
            <ChartPlaceholder height={220} />
          )}
        </Card>

        <Card id="workouts-form" title="Form" caption={`Fitness פחות Fatigue. מתחת לאפס: סופגים עבודה. אדום: מעבר לקו ה-30-, אזור סיכון.${loadSpan}`}>
          {days ? days.length ? <Chart build={buildForm} height={160} /> : <NoLoad /> : <ChartPlaceholder height={160} />}
        </Card>

        <ActivityList id="workouts-list" />

        <CalendarCard />
      </div>
    </>
  );
}
