import { useEffect, useState } from "react";
import { authenticate, NotMemberError } from "./api";
import { TabBar, type Tab } from "./components/TabBar";
import { RangeProvider } from "./range";
import { Goals } from "./screens/Goals";
import { Health } from "./screens/Health";
import { Records } from "./screens/Records";
import { Today } from "./screens/Today";
import { Workouts } from "./screens/Workouts";
import { tg } from "./telegram";

type Session =
  | { state: "loading" }
  | { state: "outside" } // opened in a normal browser
  | { state: "not-member" } // Telegram knows them, we don't (403)
  | { state: "error" }
  | { state: "ready"; name: string; garminLinked: boolean };

function useSession(): Session {
  const [session, setSession] = useState<Session>({ state: "loading" });

  useEffect(() => {
    if (!tg) {
      // `npm run dev` in a normal browser: show the screens without data,
      // so layout work doesn't need a phone. Production builds never do this.
      setSession(import.meta.env.DEV ? { state: "ready", name: "", garminLinked: true } : { state: "outside" });
      return;
    }
    authenticate()
      .then((me) => setSession({ state: "ready", name: me.first_name, garminLinked: me.garmin_linked }))
      .catch((err) => setSession({ state: err instanceof NotMemberError ? "not-member" : "error" }));
  }, []);

  return session;
}

/**
 * Telegram's own back button (top of the window) instead of one drawn in
 * the page. For now there are only tabs, so "back" means home; deeper
 * pages (a single workout) will push onto this later.
 */
function useTelegramBack(tab: Tab, goHome: () => void) {
  useEffect(() => {
    if (!tg) return;
    if (tab === "today") {
      tg.BackButton.hide();
      return;
    }
    tg.BackButton.show();
    tg.BackButton.onClick(goHome);
    return () => tg?.BackButton.offClick(goHome);
  }, [tab, goHome]);
}

function Message({ title, text }: { title: string; text: string }) {
  return (
    <main className="center">
      <div>
        <h1>{title}</h1>
        <p>{text}</p>
      </div>
    </main>
  );
}

export function App() {
  const session = useSession();
  const [tab, setTab] = useState<Tab>("today");
  const [goHome] = useState(() => () => setTab("today"));
  useTelegramBack(tab, goHome);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [tab]);

  switch (session.state) {
    case "loading":
      return <Message title="…" text="" />;
    case "outside":
      return <Message title="פתח אותי מתוך הבוט" text="הדשבורד עובד רק מתוך טלגרם." />;
    case "not-member":
      return <Message title="עוד לא מחובר" text="פתח את הבוט וחבר את Garmin." />;
    case "error":
      return <Message title="משהו השתבש" text="נסה לסגור ולפתוח שוב." />;
  }

  if (!session.garminLinked) {
    return <Message title={`שלום ${session.name}`.trim()} text="כדי לראות נתונים, חבר את Garmin בבוט." />;
  }

  return (
    <RangeProvider>
      <main className="app">
        {tab === "today" && <Today name={session.name} />}
        {tab === "workouts" && <Workouts />}
        {tab === "health" && <Health />}
        {tab === "goals" && <Goals />}
        {tab === "records" && <Records />}
      </main>
      <TabBar active={tab} onChange={setTab} />
    </RangeProvider>
  );
}
