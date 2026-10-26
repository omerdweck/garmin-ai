import { useEffect } from "react";
import { refreshUnread, useUnread } from "../screens/Notifications";
import "../screens/notifications.css";
import { tg } from "../telegram";
import { Icon } from "./icons";

/** Often enough that a post-workout message shows up while the app is open; cheap (one count). */
const POLL_MS = 60_000;

/**
 * The top bar of every tab: the Garmin Assistant wordmark on the left, the
 * person's Telegram photo and the notifications bell on the right. The bell
 * carries the number of unread messages from the bot and opens the
 * notifications screen.
 */
export function AppBar({ name, onAlerts }: { name: string; onAlerts(): void }) {
  const unread = useUnread();
  const photo = tg?.initDataUnsafe.user?.photo_url;
  const initial = (name || tg?.initDataUnsafe.user?.first_name || "").trim().charAt(0);

  // Every minute, and the moment the app comes back to the front: a phone
  // that slept through the poll would otherwise show an old number until the
  // next one.
  useEffect(() => {
    void refreshUnread();
    const timer = window.setInterval(() => void refreshUnread(), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshUnread();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, []);

  const count = unread ?? 0;
  return (
    <div className="app-bar">
      <div className="app-actions">
        <span className="avatar" aria-hidden="true">
          {photo ? <img src={photo} alt="" referrerPolicy="no-referrer" /> : initial || <Icon name="heart" size={18} />}
        </span>
        <button className="icon-button" onClick={onAlerts} aria-label={count ? `התראות, ${count} לא נקראו` : "התראות"}>
          <Icon name="bell" size={20} />
          {count > 0 && <span className="badge count num" aria-hidden="true">{count > 99 ? "99+" : count}</span>}
        </button>
      </div>
      <div className="brand" aria-label="Garmin Assistant"><b>Garmin</b><span>Assistant</span></div>
    </div>
  );
}
