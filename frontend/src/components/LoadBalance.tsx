/**
 * "מאזן עומס": today's Form (Fitness - Fatigue) as a place on a line rather
 * than a bare number. Four colored zones, a marker for today, the state in
 * one word and what to do about it. The number itself is there, but small:
 * -12 means nothing to most people, "בונה כושר" does.
 *
 * The zones' edges match the backend's load_zone (dashboard_stats.py).
 */

export type LoadBalanceData = {
  form: number;
  fitness: number;
  fatigue: number;
  zone: "high" | "building" | "balanced" | "fresh";
  word: string;
  advice: string;
};

// The drawn range: wide enough for nearly every real day; a form beyond it
// pins the marker to the end rather than stretching the scale.
const MIN = -50;
const MAX = 25;
const ZONES: { key: LoadBalanceData["zone"]; from: number; to: number; label: string }[] = [
  { key: "high", from: MIN, to: -30, label: "עומס גבוה" },
  { key: "building", from: -30, to: -10, label: "בנייה" },
  { key: "balanced", from: -10, to: 5, label: "מאוזן" },
  { key: "fresh", from: 5, to: MAX, label: "רענן" },
];

const at = (value: number) => ((Math.max(MIN, Math.min(MAX, value)) - MIN) / (MAX - MIN)) * 100;

export function LoadGauge({ data }: { data: LoadBalanceData }) {
  return (
    <div className={`load-gauge zone-${data.zone}`}>
      {/* A number line, so left to right like the Form chart: more fatigue on the left. */}
      <div className="load-track" dir="ltr">
        {ZONES.map((z) => (
          <span
            key={z.key}
            className={`load-zone load-${z.key}${z.key === data.zone ? " current" : ""}`}
            style={{ flex: z.to - z.from }}
          />
        ))}
        <span className="load-marker" style={{ left: `${at(data.form)}%` }} />
      </div>
      <div className="load-labels" dir="ltr">
        {ZONES.map((z) => (
          <span key={z.key} style={{ flex: z.to - z.from }} className={z.key === data.zone ? "current" : undefined}>
            {z.label}
          </span>
        ))}
      </div>
    </div>
  );
}
