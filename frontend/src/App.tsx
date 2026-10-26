import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { authenticate, NotMemberError } from "./api";
import { AppBar } from "./components/AppBar";
import { TabBar, type Tab } from "./components/TabBar";
import { RangeProvider } from "./range";
import { Records } from "./screens/Records";
import { Today } from "./screens/Today";
import { activityFromUrl, OpenActivityContext } from "./nav";
import { tg } from "./telegram";

// The charts library is most of the download (~280 KB). Loaded only when a
// tab with charts opens, so the home tab appears at once on a slow phone.
const Workouts = lazy(() => import("./screens/Workouts").then((m) => ({ default: m.Workouts })));
const Health = lazy(() => import("./screens/Health").then((m) => ({ default: m.Health })));
const Goals = lazy(() => import("./screens/Goals").then((m) => ({ default: m.Goals })));
const ActivityPage = lazy(() => import("./screens/ActivityPage").then((m) => ({ default: m.ActivityPage })));

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
 * the page. An open workout closes back to the tab it came from; any tab
 * other than home goes back home.
 */
function useTelegramBack(tab: Tab, activityOpen: boolean, back: () => void) {
  useEffect(() => {
    if (!tg) return;
    if (tab === "today" && !activityOpen) {
      tg.BackButton.hide();
      return;
    }
    tg.BackButton.show();
    tg.BackButton.onClick(back);
    return () => tg?.BackButton.offClick(back);
  }, [tab, activityOpen, back]);
}

function Message({ title, text }: { title: string; text: string }) {
  return (
    <main className="center">
      <div>
        <div className="brand"><b>Garmin</b><span>Assistant</span></div>
        <h1>{title}</h1>
        <p>{text}</p>
      </div>
    </main>
  );
}

export function App() {
  const session = useSession();
  // Opened from a post-workout message (?activity=123): straight to that workout.
  const [activityId, setActivityId] = useState<number | null>(activityFromUrl);
  const [tab, setTab] = useState<Tab>(() => (activityFromUrl() ? "workouts" : "today"));
  const back = useCallback(() => {
    if (activityId !== null) setActivityId(null);
    else setTab("today");
  }, [activityId]);
  useTelegramBack(tab, activityId !== null, back);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [tab, activityId]);

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

  if (activityId !== null) {
    return (
      <Suspense fallback={<div className="skeleton" style={{ height: 420, margin: 16 }} />}>
        <ActivityPage id={activityId} />
      </Suspense>
    );
  }

  return (
    <OpenActivityContext.Provider value={setActivityId}>
    <RangeProvider>
      <main className="app">
        <AppBar name={session.name} onAlerts={() => setTab("health")} />
        {tab === "today" && <Today name={session.name} />}
        <Suspense fallback={<div className="skeleton" style={{ height: 240, marginTop: 60 }} />}>
          {tab === "workouts" && <Workouts />}
          {tab === "health" && <Health />}
          {tab === "goals" && <Goals />}
        </Suspense>
        {tab === "records" && <Records />}
      </main>
      <TabBar active={tab} onChange={setTab} />
    </RangeProvider>
    </OpenActivityContext.Provider>
  );
}
