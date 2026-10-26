/**
 * The notifications screen behind the bell: everything the bot sent without
 * being asked - a workout's summary, a metric out of its normal range, the
 * weekly report - newest first, grouped by day.
 *
 * It lies over the current tab instead of replacing it (App.tsx), so
 * closing it puts the user back exactly where they were, scroll and all.
 *
 * The unread count is shared with the bell through a tiny store here
 * rather than through useApi: the bell polls it, this screen changes it,
 * and both must show the same number without waiting for the next poll.
 */

import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { apiGet, apiPost } from "../api";
import { Icon, type IconName } from "../components/icons";
import { hapticSelect } from "../telegram";
import "./notifications.css";

export type NotificationLink =
  | { type: "activity"; id: number }
  | { type: "tab"; tab: string; focus?: string }
  | null;

type Item = {
  id: number;
  kind: string;
  text: string;
  link: NotificationLink;
  created_at: string;
  read: boolean;
};

type Page = { items: Item[]; unread: number; more: boolean; backfilling?: boolean };

// ---- the unread count, shared with the bell ---------------------------------

let unread: number | null = null;
const listeners = new Set<() => void>();

function setUnread(value: number) {
  unread = Math.max(0, value);
  listeners.forEach((fn) => fn());
}

export function refreshUnread(): Promise<void> {
  return apiGet<{ unread: number }>("/api/notifications/unread")
    .then((r) => setUnread(r.unread))
    .catch(() => {
      /* the badge keeps its last number; the next poll tries again */
    });
}

/** null until the first answer arrives - the bell shows no badge rather than a guessed one. */
export function useUnread(): number | null {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => unread,
  );
}

// ---- how each kind looks ----------------------------------------------------

const KINDS: Record<string, { icon: IconName; color: string; label: string }> = {
  post_workout: { icon: "pulse", color: "var(--orange)", label: "אימון" },
  anomaly: { icon: "alert", color: "var(--bad)", label: "חריגה במדדים" },
  weekly_report: { icon: "calendar", color: "var(--sport-running)", label: "סיכום שבועי" },
  daily_summary: { icon: "bars", color: "var(--sport-other)", label: "סיכום יומי" },
  record: { icon: "trophy", color: "var(--sport-strength)", label: "שיא אישי" },
  reminder: { icon: "clock", color: "var(--sport-swimming)", label: "תזכורת" },
  watch: { icon: "watch", color: "var(--zone-1)", label: "השעון" },
  account: { icon: "zap", color: "var(--warn)", label: "חיבור" },
};
const OTHER = { icon: "bell" as IconName, color: "var(--muted)", label: "הודעה" };

// ---- the bot's text, as React ------------------------------------------------

/*
 * The text is stored exactly as Telegram received it: legacy Markdown
 * (*bold*, _italic_, `code`, [label](url)), and possibly a stray HTML tag.
 * Turned into plain React elements piece by piece - never innerHTML, since
 * part of every message is written by a model.
 */
const INLINE = /(\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)|<(b|strong|i|em|code)>[^<\n]*<\/\2>)/g;
const ENTITIES: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };

function decode(text: string): string {
  return text.replace(/<\/?[a-z][^>]*>/gi, "").replace(/&(amp|lt|gt|quot|#39);/g, (m) => ENTITIES[m] ?? m);
}

function inline(line: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of line.matchAll(INLINE)) {
    const token = match[0];
    const at = match.index ?? 0;
    if (at > last) out.push(decode(line.slice(last, at)));
    const k = `${key}-${n++}`;
    if (token.startsWith("*")) out.push(<b key={k}>{decode(token.slice(1, -1))}</b>);
    else if (token.startsWith("_")) out.push(<i key={k}>{decode(token.slice(1, -1))}</i>);
    else if (token.startsWith("`")) out.push(<code key={k}>{token.slice(1, -1)}</code>);
    else if (token.startsWith("[")) out.push(decode(token.slice(1, token.indexOf("]"))));
    else {
      const tag = match[2];
      const inner = decode(token.replace(/^<[^>]+>|<\/[^>]+>$/g, ""));
      out.push(tag === "b" || tag === "strong" ? <b key={k}>{inner}</b> : tag === "code" ? <code key={k}>{inner}</code> : <i key={k}>{inner}</i>);
    }
    last = at + token.length;
  }
  if (last < line.length) out.push(decode(line.slice(last)));
  return out;
}

export function BotText({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").replace(/<br\s*\/?>/gi, "\n").split("\n");
  return (
    <>
      {lines.map((line, i) => (
        <Fragment key={i}>
          {i > 0 && <br />}
          {inline(line, String(i))}
        </Fragment>
      ))}
    </>
  );
}

// ---- dates ---------------------------------------------------------------------

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(d: Date, now: Date): string {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(now)) return "היום";
  if (dayKey(d) === dayKey(yesterday)) return "אתמול";
  return d.toLocaleDateString("he-IL", {
    weekday: "long",
    day: "numeric",
    month: "long",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  });
}

/** "עכשיו", "לפני 5 דק׳", "לפני 3 שע׳" today; the clock time on older days, which are already under a date. */
function relative(d: Date, now: Date): string {
  const minutes = Math.floor((now.getTime() - d.getTime()) / 60_000);
  if (dayKey(d) === dayKey(now)) {
    if (minutes < 1) return "עכשיו";
    if (minutes < 60) return `לפני ${minutes} דק׳`;
    return `לפני ${Math.floor(minutes / 60)} שע׳`;
  }
  return d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
}

// ---- one notification ------------------------------------------------------------

function Row({ item, now, onOpen }: { item: Item; now: Date; onOpen(item: Item): void }) {
  const kind = KINDS[item.kind] ?? OTHER;
  const textRef = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);
  const created = new Date(item.created_at);

  // Only offer "קרא עוד" when four lines really cut something off.
  useLayoutEffect(() => {
    const el = textRef.current;
    if (el && !expanded) setClamped(el.scrollHeight > el.clientHeight + 1);
  }, [item.text, expanded]);

  return (
    <li
      className={`notif-item${item.read ? "" : " unread"}`}
      onClick={() => {
        // Nowhere to go: the tap reads the whole message instead.
        if (!item.link && (clamped || expanded)) setExpanded((x) => !x);
        onOpen(item);
      }}
    >
      <button className="notif-open" aria-label={`${kind.label}${item.read ? "" : ", לא נקראה"}`}>
        <span className="notif-icon" style={{ color: kind.color }}>
          <Icon name={kind.icon} size={20} stroke={2} />
        </span>
        <p ref={textRef} className={`notif-text${expanded ? "" : " clamp"}`} dir="auto">
          {/* Blank lines between paragraphs read well in a chat, but would
              spend half of the four clamped lines on nothing. */}
          <BotText text={expanded ? item.text : item.text.replace(/\n\s*\n+/g, "\n")} />
        </p>
      </button>
      <div className="notif-meta">
        <time dateTime={item.created_at}>{relative(created, now)}</time>
        {(clamped || expanded) && (
          <button
            className="notif-more"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((x) => !x);
            }}
          >
            {expanded ? "הצג פחות" : "קרא עוד"}
          </button>
        )}
      </div>
      {!item.read && <span className="notif-dot" aria-hidden="true" />}
    </li>
  );
}

function Skeleton() {
  return (
    <ul className="notif-list" aria-busy="true" aria-label="טוען התראות">
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="notif-item notif-skeleton">
          <span className="skeleton notif-icon" />
          <div>
            <div className="skeleton" style={{ height: 14, width: "86%" }} />
            <div className="skeleton" style={{ height: 14, width: "64%", marginTop: 8 }} />
            <div className="skeleton" style={{ height: 11, width: 60, marginTop: 12 }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---- the screen -------------------------------------------------------------------

const PAGE = 20;

export function Notifications({ onNavigate }: { onNavigate(link: NotificationLink): void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const count = useUnread();
  const now = new Date();

  const loadFirst = useCallback((retryForBackfill: boolean) => {
    setError(false);
    return apiGet<Page>(`/api/notifications?limit=${PAGE}`)
      .then((page) => {
        setItems(page.items);
        setMore(page.more);
        setUnread(page.unread);
        // Messages from before the bell existed are being filed right now
        // by a worker; one second look shortly after beats an empty screen.
        if (retryForBackfill && page.backfilling && page.items.length === 0) {
          window.setTimeout(() => void loadFirst(false), 2500);
        }
      })
      .catch(() => setError(true));
  }, []);

  useEffect(() => {
    void loadFirst(true);
  }, [loadFirst]);

  const loadMore = () => {
    if (!items?.length) return;
    setLoadingMore(true);
    apiGet<Page>(`/api/notifications?limit=${PAGE}&before=${items[items.length - 1].id}`)
      .then((page) => {
        setItems((prev) => [...(prev ?? []), ...page.items]);
        setMore(page.more);
        setUnread(page.unread);
      })
      .catch(() => {})
      .finally(() => setLoadingMore(false));
  };

  const markAll = () => {
    hapticSelect();
    setItems((prev) => prev?.map((i) => ({ ...i, read: true })) ?? prev);
    setUnread(0);
    apiPost<{ unread: number }>("/api/notifications/read", { all: true })
      .then((r) => setUnread(r.unread))
      .catch(() => void refreshUnread());
  };

  const open = (item: Item) => {
    hapticSelect();
    if (!item.read) {
      setItems((prev) => prev?.map((i) => (i.id === item.id ? { ...i, read: true } : i)) ?? prev);
      setUnread((unread ?? 1) - 1);
      // Not awaited: the user is already on their way to the workout.
      apiPost<{ unread: number }>("/api/notifications/read", { ids: [item.id] })
        .then((r) => setUnread(r.unread))
        .catch(() => void refreshUnread());
    }
    if (item.link) onNavigate(item.link);
  };

  // Grouped by the day it arrived, in the phone's own time zone.
  const groups: { label: string; items: Item[] }[] = [];
  for (const item of items ?? []) {
    const label = dayLabel(new Date(item.created_at), now);
    if (groups.length && groups[groups.length - 1].label === label) groups[groups.length - 1].items.push(item);
    else groups.push({ label, items: [item] });
  }

  return (
    <div className="notif-screen" role="dialog" aria-modal="true" aria-labelledby="notif-title">
      <div className="notif-page">
        <header className="notif-header">
          <h1 id="notif-title" className="page-title">התראות</h1>
          {items?.length !== 0 && (
            <button className="notif-markall" onClick={markAll} disabled={!count}>
              <Icon name="check" size={16} stroke={2.4} />
              סמן הכל כנקרא
            </button>
          )}
        </header>

        {items === null && !error && <Skeleton />}

        {error && (
          <div className="notif-empty">
            <p>ההתראות לא נטענו.</p>
            <button className="notif-pill" onClick={() => void loadFirst(false)}>נסה שוב</button>
          </div>
        )}

        {items && items.length === 0 && (
          <div className="notif-empty">
            <span className="notif-empty-icon"><Icon name="bell" size={28} /></span>
            <h2>אין התראות עדיין</h2>
            <p>כשהמאמן ישלח לך משהו ביוזמתו - סיכום של אימון, שינוי חריג במדדים או הסיכום השבועי - זה יופיע כאן.</p>
          </div>
        )}

        {groups.map((group) => (
          <section key={group.label} className="notif-group">
            <h2 className="notif-day">{group.label}</h2>
            <ul className="notif-list">
              {group.items.map((item) => <Row key={item.id} item={item} now={now} onOpen={open} />)}
            </ul>
          </section>
        ))}

        {more && (
          <div className="notif-footer">
            <button className="notif-pill" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "טוען…" : "עוד"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
