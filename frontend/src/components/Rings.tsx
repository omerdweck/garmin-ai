/**
 * Three concentric activity rings, Apple Fitness style. Each ring is a
 * faint track of its own hue and an arc for the progress; past 100% the
 * arc keeps going for a second lap on top of the first, with a shadow at
 * its head so the overlap reads. A ring that closes gets a short glow.
 *
 * Colors come from CSS (--ring-color on each ring, set in home.css), so
 * light and dark theme each get a hue tuned for their background.
 */

import { useEffect, useState } from "react";

export type RingSpec = {
  key: string;
  /** progress 0..n - 1 is a closed ring, 1.4 a closed ring and 40% of a second lap */
  progress: number;
  label: string;
};

const SIZE = 200;
const WIDTH = 20;
const GAP = 4;
// More than two laps draws as two: the ring is closed twice over either way.
const MAX_LAPS = 2;

export function Rings({ rings, size = 168 }: { rings: RingSpec[]; size?: number }) {
  // Start empty and fill on the next frame, so the arcs animate in from
  // zero instead of appearing already drawn.
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)));
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <svg
      className="rings"
      width={size}
      height={size}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      role="img"
      aria-label={rings.map((r) => `${r.label}: ${Math.round(r.progress * 100)}%`).join(", ")}
    >
      {rings.map((ring, i) => {
        const r = SIZE / 2 - WIDTH / 2 - 2 - i * (WIDTH + GAP);
        const c = 2 * Math.PI * r;
        const p = shown ? Math.max(0, Math.min(ring.progress, MAX_LAPS)) : 0;
        const first = Math.min(p, 1);
        const second = Math.max(0, p - 1);
        const closed = shown && ring.progress >= 1;
        return (
          <g
            key={ring.key}
            className={`ring ring-${ring.key}${closed ? " closed" : ""}`}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          >
            <circle className="ring-track" cx={SIZE / 2} cy={SIZE / 2} r={r} strokeWidth={WIDTH} />
            <circle
              className="ring-arc"
              cx={SIZE / 2} cy={SIZE / 2} r={r} strokeWidth={WIDTH}
              strokeDasharray={c}
              // Inline style rather than attributes: CSS transitions are what animate them.
              // Hidden when empty: a round cap on a zero-length arc would still draw a dot.
              style={{ strokeDashoffset: c * (1 - first), opacity: first > 0 ? 1 : 0 }}
            />
            {/* Always mounted, so the second lap animates in after the first instead of popping up. */}
            <circle
              className="ring-arc ring-lap"
              cx={SIZE / 2} cy={SIZE / 2} r={r} strokeWidth={WIDTH}
              strokeDasharray={c}
              style={{ strokeDashoffset: c * (1 - second), opacity: second > 0 ? 1 : 0 }}
            />
          </g>
        );
      })}
    </svg>
  );
}
