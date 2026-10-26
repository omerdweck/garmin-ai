import { useEffect, useState, type CSSProperties } from "react";
import { apiGet } from "../api";
import { distance, duration, when } from "../format";
import { useOpenActivity } from "../nav";
import { SPORTS } from "../sports";
import "../screens/activity.css";
import { Icon } from "./icons";
import { Card } from "./ui";

export type ActivityRow = {
  id: number; name: string; sport: string; type: string; start: string;
  seconds: number; meters: number; avg_hr: number | null; manual: boolean;
};

const PAGE = 10;
const sportOf = (key: string) => SPORTS.find((s) => s.key === key) ?? SPORTS[SPORTS.length - 1];

/** The latest workouts, newest first; tap one to open its page. */
export function ActivityList() {
  const open = useOpenActivity();
  const [rows, setRows] = useState<ActivityRow[] | null>(null);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = async (before?: number) => {
    setLoading(true);
    try {
      const body = await apiGet<{ activities: ActivityRow[]; more: boolean }>(
        `/api/activities?limit=${PAGE}${before ? `&before=${before}` : ""}`,
      );
      setRows((prev) => [...(before ? prev ?? [] : []), ...body.activities]);
      setMore(body.more);
    } catch {
      setRows((prev) => prev ?? []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <Card title="האימונים האחרונים" caption="לחיצה על אימון פותחת מפה, גרפים ו-splits.">
      {rows === null ? (
        <div className="skeleton" style={{ height: 180 }} />
      ) : rows.length === 0 ? (
        <p className="stat-sub">עוד אין אימונים.</p>
      ) : (
        <ul className="list activity-list">
          {rows.map((a) => {
            const sport = sportOf(a.sport);
            return (
              <li key={a.id}>
                <button onClick={() => open(a.id)}>
                  <span className="icon-circle" style={{ "--tone": sport.color } as CSSProperties}>{sport.icon}</span>
                  <span className="activity-text">
                    <span className="activity-name">{a.name || sport.label}{a.manual ? " ✍️" : ""}</span>
                    <span className="stat-sub">{when(a.start)}</span>
                  </span>
                  <span className="activity-numbers">
                    {a.meters > 0 && <span className="num">{distance(a.meters, a.sport)}</span>}
                    <span className="stat-sub num">{duration(a.seconds)}</span>
                  </span>
                  <span className="chevron" aria-hidden="true"><Icon name="chevronLeft" size={18} /></span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {more && (
        <button className="more" disabled={loading} onClick={() => load(rows?.at(-1)?.id)}>
          {loading ? "טוען…" : "עוד אימונים"}
        </button>
      )}
    </Card>
  );
}
