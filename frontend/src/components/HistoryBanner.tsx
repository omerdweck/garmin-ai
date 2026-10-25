import { useEffect, useState } from "react";
import { apiGet } from "../api";

type HistoryStatus = { status: "none" | "pending" | "running" | "done" | "paused"; percent: number };

const POLL_MS = 60_000;

/**
 * "📥 Loading your history, 63%" while the nightly import is still going.
 * Hidden once it's done, and for users it hasn't started for.
 */
export function HistoryBanner() {
  const [state, setState] = useState<HistoryStatus | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await apiGet<HistoryStatus>("/api/history-status");
        if (!alive) return;
        setState(next);
        if (next.status === "pending" || next.status === "running") {
          timer = window.setTimeout(load, POLL_MS);
        }
      } catch {
        /* a banner is not worth an error screen */
      }
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  if (!state || state.status === "none" || state.status === "done") return null;

  if (state.status === "paused") {
    return <div className="banner">📥 ייבוא ההיסטוריה מחכה לחיבור מחדש של Garmin בבוט</div>;
  }

  return (
    <div className="banner" role="status">
      📥 טוען את השנה האחרונה שלך מ-Garmin, <span className="num">{state.percent}%</span>
      <div className="bar">
        <span style={{ width: `${Math.max(state.percent, 3)}%` }} />
      </div>
    </div>
  );
}
