/** The few building blocks every screen is made of. */

import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { refreshAll } from "../hooks/useApi";
import { useCountUp } from "../hooks/useCountUp";
import "../polish.css";
import { hapticSelect, tg } from "../telegram";
import { Icon, type IconName } from "./icons";
import { PullToRefresh } from "./PullToRefresh";
import { SyncStatus } from "./SyncStatus";

/**
 * Every tab starts with this, so whatever every tab should have lives
 * here: the "updated N minutes ago" line under the subtitle, and pull to
 * refresh - mounted with the header, so a new tab gets both for free.
 */
export function PageHeader({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <header className="page-header">
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="page-subtitle">{subtitle}</p>}
      <SyncStatus />
      <PullToRefresh />
    </header>
  );
}

/**
 * What makes a card tappable: a button role (it's a whole card, and a
 * <button> can't hold the headings and lists inside it), keyboard
 * activation, a tick of haptics, and the chevron that says "this goes
 * somewhere". Without onClick, nothing - a chevron that does nothing is
 * worse than none.
 */
function tappable(onClick?: () => void, label?: string) {
  if (!onClick) return {};
  const go = () => {
    hapticSelect();
    onClick();
  };
  return {
    role: "button",
    tabIndex: 0,
    "aria-label": label,
    onClick: go,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        go();
      }
    },
  };
}

function Chevron() {
  return (
    <span className="card-chevron" aria-hidden="true">
      <Icon name="chevronLeft" size={18} stroke={2.2} />
    </span>
  );
}

/**
 * Every chart card has a one-line explanation under its title - what it
 * shows and how to read it. It's a required prop on purpose.
 */
export function Card({
  id,
  title,
  icon,
  caption,
  soon,
  className,
  onClick,
  children,
}: {
  /** A target for nav.openTab(tab, id). */
  id?: string;
  title?: string;
  /** A small icon after the title, in the given color. */
  icon?: { name: IconName; color: string };
  caption: string;
  soon?: string;
  className?: string;
  /** Makes the whole card a link somewhere, with a chevron. */
  onClick?: () => void;
  children?: ReactNode;
}) {
  const classes = ["card", onClick && "tappable", className].filter(Boolean).join(" ");
  return (
    <section id={id} className={classes} {...tappable(onClick, title)}>
      {onClick && <Chevron />}
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
export function Stat({ id, icon, tone, label, value, unit, delta, sub, onClick }: {
  id?: string;
  icon: IconName;
  tone: string;
  label: string;
  value?: string;
  unit?: string;
  delta?: Delta;
  sub?: string;
  /** Makes the tile a link somewhere, with a chevron. */
  onClick?: () => void;
}) {
  const count = useCountUp<HTMLDivElement>();
  return (
    <section id={id} className={`card stat-card${onClick ? " tappable" : ""}`} {...tappable(onClick, label)}>
      {onClick && <Chevron />}
      <span className="icon-circle" style={{ "--tone": tone } as CSSProperties}>
        <Icon name={icon} size={21} />
      </span>
      {value === undefined ? (
        <div className="skeleton" style={{ height: 32, width: "60%", marginTop: 14 }} />
      ) : (
        <div className="stat-value" style={{ marginTop: 14 }} ref={count}>
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
export function HeroCard({ id, kicker, title, sub, progress, stats, onClick, children }: {
  id?: string;
  kicker: string;
  title: ReactNode;
  sub?: ReactNode;
  /** 0..1, or undefined for no bar. */
  progress?: number;
  stats?: HeroStat[];
  /** Makes the card a link somewhere, with a chevron. */
  onClick?: () => void;
  children?: ReactNode;
}) {
  const count = useCountUp<HTMLDivElement>();
  const pct = progress === undefined ? null : Math.round(Math.max(0, Math.min(1, progress)) * 100);
  return (
    <section id={id} className={`hero-card${onClick ? " tappable" : ""}`} {...tappable(onClick, kicker)}>
      {onClick && <Chevron />}
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
        <div className="hero-stats" ref={count}>
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

/** The two things an empty or failed card can offer. */
export const RETRY = { label: "נסה שוב", run: () => void refreshAll() };
// The Mini App is opened from the bot's chat; closing it lands right there.
export const BACK_TO_BOT = { label: "חזרה לבוט", run: () => tg?.close() };

/**
 * Instead of a bare "no data" line: an icon, one sentence that says what
 * to do about it, and at most one button. `card` draws it as a card of
 * its own (a whole tab that failed); without it, it sits inside one.
 * "Back to the bot" is hidden outside Telegram, where there's no bot.
 */
export function EmptyState({ icon = "bars", title, text, action, card }: {
  icon?: IconName;
  title?: string;
  text: string;
  action?: { label: string; run(): void };
  card?: boolean;
}) {
  const shown = action && (action !== BACK_TO_BOT || tg) ? action : undefined;
  return (
    <div className={`empty-state${card ? " card" : ""}`} role={action === RETRY ? "alert" : undefined}>
      <span className="empty-icon" aria-hidden="true"><Icon name={icon} size={22} /></span>
      <div className="empty-text">
        {title && <strong>{title}</strong>}
        <span>{text}</span>
      </div>
      {shown && (
        <button className="pill-button" onClick={() => { hapticSelect(); shown.run(); }}>
          {shown.label}
        </button>
      )}
    </div>
  );
}
