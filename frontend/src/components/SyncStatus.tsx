import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "../api";
import { onRefresh, refreshAll } from "../hooks/useApi";
import { hapticSelect } from "../telegram";

type Status = { last_synced_at: string | null; syncing: boolean; linked: boolean };
type SyncResult = "started" | "syncing" | "fresh" | "busy" | "unlinked";

const POLL_MS = 10_000;
// The scheduled sync runs twice a day; past this, something is wrong
// (the watch, the login) and the line turns orange to say so.
const STALE_HOURS = 26;

/** "עודכן לפני 12 דק׳" - Hebrew's dual forms for one and two hours/days, numbers otherwise. */
export function ago(iso: string, now: number): string {
  const minutes = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000));
  if (minutes < 1) return "עודכן הרגע";
  if (minutes < 60) return `עודכן לפני ${minutes} דק׳`;
  const hours = Math.floor(minutes / 60);
  if (hours === 1) return "עודכן לפני שעה";
  if (hours === 2) return "עודכן לפני שעתיים";
  if (hours < 24) return `עודכן לפני ${hours} שעות`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "עודכן אתמול";
  if (days === 2) return "עודכן לפני יומיים";
  return `עודכן לפני ${days} ימים`;
}

const NOTES: Partial<Record<SyncResult, string>> = {
  fresh: "הנתונים כבר עדכניים",
  busy: "Garmin עמוסים כרגע, נסה שוב בעוד כמה דקות",
};

function RefreshGlyph({ spinning }: { spinning: boolean }) {
  return (
    <svg className={`icon-svg${spinning ? " spin" : ""}`} width={14} height={14} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12a9 9 0 1 1-2.64-6.36L21 8 M21 3v5h-5" />
    </svg>
  );
}

/**
 * How fresh the numbers on screen are, under every tab's title - "is
 * this today's sleep or yesterday's" is the first question when a number
 * looks off. While a sync runs it polls, and when it finishes it reloads
 * the screen, so the new numbers appear without the user doing anything.
 */
export function SyncStatus() {
  const [status, setStatus] = useState<Status | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const wasSyncing = useRef(false);

  const load = useCallback(async () => {
    try {
      const next = await apiGet<Status>("/api/sync-status");
      setStatus(next);
      setNow(Date.now());
      const finished = wasSyncing.current && !next.syncing;
      wasSyncing.current = next.syncing;
      if (finished) refreshAll();
    } catch {
      /* a status line is not worth an error */
    }
  }, []);

  useEffect(() => {
    load();
    return onRefresh(load);
  }, [load]);

  // Poll only while something is running; otherwise just keep "N minutes ago" true.
  useEffect(() => {
    const timer = window.setInterval(status?.syncing ? load : () => setNow(Date.now()), status?.syncing ? POLL_MS : 30_000);
    return () => window.clearInterval(timer);
  }, [status?.syncing, load]);

  useEffect(() => {
    if (!note) return;
    const timer = window.setTimeout(() => setNote(null), 4000);
    return () => window.clearTimeout(timer);
  }, [note]);

  const syncNow = async () => {
    hapticSelect();
    setNote(null);
    try {
      const { status: result } = await apiPost<{ status: SyncResult }>("/api/sync");
      if (result === "started" || result === "syncing") {
        setStatus((s) => (s ? { ...s, syncing: true } : s));
        wasSyncing.current = true;
      }
      setNote(NOTES[result] ?? null);
    } catch {
      setNote("לא הצלחתי להתחיל סנכרון");
    }
  };

  if (!status) return null;
  const stale = status.last_synced_at !== null && now - new Date(status.last_synced_at).getTime() > STALE_HOURS * 3_600_000;

  return (
    <div className={`sync-status${stale ? " stale" : ""}`} role="status" aria-live="polite">
      {status.syncing ? <RefreshGlyph spinning /> : <span className="sync-dot" aria-hidden="true" />}
      <span>{status.syncing ? "מסנכרן עם Garmin…" : status.last_synced_at ? ago(status.last_synced_at, now) : "עוד לא סונכרן"}</span>
      {note ? (
        <span className="sync-note">· {note}</span>
      ) : (
        status.linked && !status.syncing && (
          <button className="sync-button" onClick={syncNow}>
            <RefreshGlyph spinning={false} />
            סנכרן עכשיו
          </button>
        )
      )}
    </div>
  );
}
