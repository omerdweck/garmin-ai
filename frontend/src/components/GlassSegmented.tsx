import type { CSSProperties, KeyboardEvent, MouseEvent } from "react";
import { hapticSelect } from "../telegram";

/**
 * A switcher in the tab bar's "liquid glass" style: a frosted pill with a
 * dark knob that slides to the chosen option. One component for every
 * switcher in the app - week/month/year, a workout's tabs, the rings' day
 * or week - so they all look and move the same.
 *
 * `role` picks the right semantics: "tablist" when the options swap the
 * content below (aria-selected), "group" for a plain choice (aria-pressed).
 */
export function GlassSegmented<K extends string>({
  options,
  value,
  onChange,
  label,
  role = "group",
  size = "md",
  className,
}: {
  options: readonly { key: K; label: string }[];
  value: K;
  onChange(key: K): void;
  label: string;
  role?: "group" | "tablist";
  size?: "sm" | "md";
  className?: string;
}) {
  const index = Math.max(0, options.findIndex((o) => o.key === value));
  // A switcher inside a tappable card must not also "tap the card".
  const stop = (e: MouseEvent | KeyboardEvent) => e.stopPropagation();
  return (
    <div
      className={["glass-seg", `glass-seg-${size}`, className].filter(Boolean).join(" ")}
      role={role}
      aria-label={label}
      onClick={stop}
      onKeyDown={stop}
      style={{ "--n": options.length, "--i": index } as CSSProperties}
    >
      <span className="glass-seg-knob" aria-hidden="true" />
      {options.map((o) => {
        const on = o.key === value;
        return (
          <button
            key={o.key}
            type="button"
            role={role === "tablist" ? "tab" : undefined}
            aria-selected={role === "tablist" ? on : undefined}
            aria-pressed={role === "group" ? on : undefined}
            onClick={() => {
              if (!on) hapticSelect();
              onChange(o.key);
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
