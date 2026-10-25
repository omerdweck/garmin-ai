/**
 * The week / month / year selector shared by every tab, and the period it
 * currently points at. `offset` 0 is the current period, 1 the one before.
 *
 * Periods, as a person in Israel thinks of them:
 *   week  - Sunday to Saturday
 *   month - the calendar month
 *   year  - the last 365 days (what the history import fills)
 */

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type RangeKind = "week" | "month" | "year";

export const RANGE_LABELS: Record<RangeKind, string> = { week: "שבוע", month: "חודש", year: "שנה" };

// How far back the arrows may go: about the year the import brings in.
const MAX_OFFSET: Record<RangeKind, number> = { week: 52, month: 12, year: 0 };

const STORAGE_KEY = "range-kind";

type RangeState = {
  kind: RangeKind;
  offset: number;
  start: Date;
  end: Date;
  label: string;
  canGoBack: boolean;
  canGoForward: boolean;
  setKind(kind: RangeKind): void;
  back(): void;
  forward(): void;
};

const RangeContext = createContext<RangeState | null>(null);

function readStoredKind(): RangeKind {
  // Storage can be missing or throw inside some WebViews - the app must
  // work without it, it only remembers the choice when it can.
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "week" || value === "month" || value === "year") return value;
  } catch {
    /* no storage */
  }
  return "week";
}

function addDays(day: Date, days: number): Date {
  const next = new Date(day);
  next.setDate(next.getDate() + days);
  return next;
}

export function periodFor(kind: RangeKind, offset: number, today: Date): { start: Date; end: Date } {
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (kind === "week") {
    const sunday = addDays(base, -base.getDay() - 7 * offset);
    return { start: sunday, end: addDays(sunday, 6) };
  }
  if (kind === "month") {
    const start = new Date(base.getFullYear(), base.getMonth() - offset, 1);
    return { start, end: new Date(start.getFullYear(), start.getMonth() + 1, 0) };
  }
  const end = addDays(base, -365 * offset);
  return { start: addDays(end, -364), end };
}

const short = (d: Date) => `${d.getDate()}.${d.getMonth() + 1}`;
const full = (d: Date) => `${short(d)}.${String(d.getFullYear()).slice(2)}`;

// A date range inside Hebrew text: without an isolate the browser's
// right-to-left layout flips it to "31.10–25.10". U+2066/U+2069 mark it
// as one left-to-right run, so it reads start-to-end like a date should.
const ltr = (text: string) => `\u2066${text}\u2069`;

function labelFor(kind: RangeKind, offset: number, start: Date, end: Date): string {
  const span = (a: string, b: string) => ltr(`${a}–${b}`);
  if (kind === "week") return offset === 0 ? `השבוע · ${span(short(start), short(end))}` : span(short(start), short(end));
  if (kind === "month") {
    const name = start.toLocaleDateString("he-IL", { month: "long", year: "numeric" });
    return offset === 0 ? `החודש · ${name}` : name;
  }
  return `12 החודשים האחרונים · ${span(full(start), full(end))}`;
}

export function RangeProvider({ children }: { children: ReactNode }) {
  const [kind, setKindState] = useState<RangeKind>(readStoredKind);
  const [offset, setOffset] = useState(0);

  const value = useMemo<RangeState>(() => {
    const { start, end } = periodFor(kind, offset, new Date());
    return {
      kind,
      offset,
      start,
      end,
      label: labelFor(kind, offset, start, end),
      canGoBack: offset < MAX_OFFSET[kind],
      canGoForward: offset > 0,
      setKind(next) {
        setKindState(next);
        setOffset(0);
        try {
          localStorage.setItem(STORAGE_KEY, next);
        } catch {
          /* no storage */
        }
      },
      back: () => setOffset((o) => Math.min(o + 1, MAX_OFFSET[kind])),
      forward: () => setOffset((o) => Math.max(o - 1, 0)),
    };
  }, [kind, offset]);

  return <RangeContext.Provider value={value}>{children}</RangeContext.Provider>;
}

export function useRange(): RangeState {
  const value = useContext(RangeContext);
  if (!value) throw new Error("useRange outside RangeProvider");
  return value;
}
