/** The few building blocks every screen is made of. */

import type { ReactNode } from "react";

export function PageHeader({ kicker, title, subtitle }: { kicker: string; title: string; subtitle?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="kicker">{kicker}</div>
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
  kicker,
  title,
  caption,
  soon,
  children,
}: {
  kicker?: string;
  title?: string;
  caption: string;
  soon?: string;
  children?: ReactNode;
}) {
  return (
    <section className="card">
      {kicker && <div className="kicker">{kicker}</div>}
      {title && <h2 className="card-title">{title}</h2>}
      <p className="card-caption">{caption}</p>
      {children}
      {soon && <span className="soon">בקרוב · {soon}</span>}
    </section>
  );
}

/** A big number with its context line. `value` undefined draws a placeholder. */
export function Stat({ label, value, sub }: { label: string; value?: string; sub?: string }) {
  return (
    <section className="card">
      <div className="kicker">{label}</div>
      {value === undefined ? (
        <div className="skeleton" style={{ height: 32, width: "60%", marginTop: 8 }} />
      ) : (
        <div className="stat-value num">{value}</div>
      )}
      {sub && <div className="stat-sub">{sub}</div>}
    </section>
  );
}

export function ChartPlaceholder({ height = 160 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} aria-hidden="true" />;
}
