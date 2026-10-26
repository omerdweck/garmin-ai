/** The few building blocks every screen is made of. */

import type { CSSProperties, ReactNode } from "react";
import { Icon, type IconName } from "./icons";

export function PageHeader({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <header className="page-header">
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="page-subtitle">{subtitle}</p>}
    </header>
  );
}

/**
 * Every chart card has a one-line explanation under its title - what it
 * shows and how to read it. It's a required prop on purpose.
 */
export function Card({
  title,
  icon,
  caption,
  soon,
  className,
  children,
}: {
  title?: string;
  /** A small icon after the title, in the given color. */
  icon?: { name: IconName; color: string };
  caption: string;
  soon?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section className={`card${className ? ` ${className}` : ""}`}>
      {title && (
        <h2 className="card-title">
          {title}
          {icon && <span style={{ color: icon.color }}><Icon name={icon.name} size={18} stroke={2.2} /></span>}
        </h2>
      )}
      <p className="card-caption">{caption}</p>
      {children}
      {soon && <span className="soon">בקרוב · {soon}</span>}
    </section>
  );
}

export type Delta = { text: string; dir: "up" | "down" | "flat" };

/**
 * A stat tile: a tinted icon circle, a big number, its label and the
 * change from the previous period. `value` undefined draws a placeholder.
 */
export function Stat({ icon, tone, label, value, unit, delta, sub }: {
  icon: IconName;
  tone: string;
  label: string;
  value?: string;
  unit?: string;
  delta?: Delta;
  sub?: string;
}) {
  return (
    <section className="card stat-card">
      <span className="icon-circle" style={{ "--tone": tone } as CSSProperties}>
        <Icon name={icon} size={21} />
      </span>
      {value === undefined ? (
        <div className="skeleton" style={{ height: 32, width: "60%", marginTop: 14 }} />
      ) : (
        <div className="stat-value" style={{ marginTop: 14 }}>
          <span className="num">{value}</span>
          {unit && <small>{unit}</small>}
        </div>
      )}
      <div className="stat-label">{label}</div>
      {delta && (
        <div className={`delta ${delta.dir}`}>
          {delta.dir === "up" ? "↑ " : delta.dir === "down" ? "↓ " : ""}
          {delta.text}
        </div>
      )}
      {sub && <div className="stat-sub" style={{ marginTop: 4 }}>{sub}</div>}
    </section>
  );
}

export type HeroStat = { value: ReactNode; label: string; state?: { text: string; color: string } };

/**
 * The one dark card at the top of a screen: an orange kicker, a headline,
 * an optional progress bar and a row of numbers split by thin lines.
 */
export function HeroCard({ kicker, title, sub, progress, stats, children }: {
  kicker: string;
  title: ReactNode;
  sub?: ReactNode;
  /** 0..1, or undefined for no bar. */
  progress?: number;
  stats?: HeroStat[];
  children?: ReactNode;
}) {
  const pct = progress === undefined ? null : Math.round(Math.max(0, Math.min(1, progress)) * 100);
  return (
    <section className="hero-card">
      <div className="hero-kicker">{kicker}</div>
      <div className="hero-title">{title}</div>
      {sub && <div className="hero-sub">{sub}</div>}
      {pct !== null && (
        <div className="hero-progress">
          <span className="num">{pct}%</span>
          <div className="track" dir="ltr"><span style={{ width: `${Math.max(pct, 2)}%` }} /></div>
        </div>
      )}
      {children}
      {stats && stats.length > 0 ? (
        <div className="hero-stats">
          {stats.map((s) => (
            <div key={s.label}>
              <div className="value">{s.value}</div>
              <div className="label">{s.label}</div>
              {s.state && <div className="state" style={{ color: s.state.color }}>{s.state.text}</div>}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ height: 20 }} />
      )}
    </section>
  );
}

export function ChartPlaceholder({ height = 160 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} aria-hidden="true" />;
}
