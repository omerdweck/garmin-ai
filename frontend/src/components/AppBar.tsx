import { useApi } from "../hooks/useApi";
import { tg } from "../telegram";
import { Icon } from "./icons";

/**
 * The top bar of every tab: the Garmin Assistant wordmark on the left, the
 * person's Telegram photo and an alerts bell on the right. The bell gets a
 * dot while the coach has an open alert, and opens the health tab.
 */
export function AppBar({ name, onAlerts }: { name: string; onAlerts(): void }) {
  const { data } = useApi<{ alert: { text: string } | null }>("/api/today");
  const photo = tg?.initDataUnsafe.user?.photo_url;
  const initial = (name || tg?.initDataUnsafe.user?.first_name || "").trim().charAt(0);
  return (
    <div className="app-bar">
      <div className="app-actions">
        <span className="avatar" aria-hidden="true">
          {photo ? <img src={photo} alt="" referrerPolicy="no-referrer" /> : initial || <Icon name="heart" size={18} />}
        </span>
        <button className="icon-button" onClick={onAlerts} aria-label={data?.alert ? "יש התראה פתוחה" : "התראות"}>
          <Icon name="bell" size={20} />
          {data?.alert && <span className="badge" />}
        </button>
      </div>
      <div className="brand" aria-label="Garmin Assistant"><b>Garmin</b><span>Assistant</span></div>
    </div>
  );
}
