import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { authenticate, NotMemberError } from "./api";
import { AppBar } from "./components/AppBar";
import { TabBar, type Tab } from "./components/TabBar";
import { RangeProvider } from "./range";
import { Notifications, type NotificationLink } from "./screens/Notifications";
import { Records } from "./screens/Records";
import { Today } from "./screens/Today";
import { activityFromUrl, NavContext, scrollToFocus, type Nav } from "./nav";
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
 * the page. The notifications screen closes back to the tab under it; an
 * open workout closes back to the tab it came from; any tab other than
 * home goes back home.
 */
function useTelegramBack(tab: Tab, layered: boolean, back: () => void) {
  useEffect(() => {
    if (!tg) return;
    if (tab === "today" && !layered) {
      tg.BackButton.hide();
      return;
    }
    tg.BackButton.show();
    tg.BackButton.onClick(back);
    return () => tg?.BackButton.offClick(back);
  }, [tab, layered, back]);
}

/**
 * While the notifications screen is up, the page under it must not scroll
 * with the finger - and must be exactly where it was when the screen
 * closes. Locking the root's overflow resets the window's scroll in some
 * engines, so the position is put back explicitly.
 */
function useScrollLock(locked: boolean) {
  useLayoutEffect(() => {
    if (!locked) return;
    const y = window.scrollY;
    document.documentElement.classList.add("notif-locked");
    return () => {
      document.documentElement.classList.remove("notif-locked");
      window.scrollTo(0, y);
    };
  }, [locked]);
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
  // The bell's screen. Drawn over the tab rather than instead of it, so
  // closing it leaves the tab - and how far down it was scrolled - as it was.
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const back = useCallback(() => {
    if (notificationsOpen) setNotificationsOpen(false);
    else if (activityId !== null) setActivityId(null);
    else setTab("today");
  }, [activityId, notificationsOpen]);
  useTelegramBack(tab, activityId !== null || notificationsOpen, back);
  useScrollLock(notificationsOpen);

  // A card that sends the user somewhere can name a spot to scroll to.
  const [focus, setFocus] = useState<string | null>(null);
  const nav = useMemo<Nav>(() => ({
    openTab(next, target) {
      setActivityId(null);
      setTab(next);
      setFocus(target ?? null);
    },
    openActivity: (id) => setActivityId(id),
  }), []);

  useEffect(() => {
    window.scrollTo(0, 0);
    if (!focus || activityId !== null) return;
    const cancel = scrollToFocus(focus);
    setFocus(null);
    return cancel;
  }, [tab, activityId]); // eslint-disable-line react-hooks/exhaustive-deps

  // A tapped notification: close the screen, then go where it points.
  const fromNotification = useCallback((link: NotificationLink) => {
    setNotificationsOpen(false);
    if (!link) return;
    if (link.type === "activity") {
      nav.openActivity(link.id);
      return;
    }
    const target = link.tab as Tab;
    if (target === tab) {
      // Same tab: nothing changes for the effect above to react to, so
      // scroll here - once the overlay is gone and the page can scroll.
      requestAnimationFrame(() => (link.focus ? scrollToFocus(link.focus) : window.scrollTo(0, 0)));
      return;
    }
    nav.openTab(target, link.focus);
  }, [nav, tab]);

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
      <NavContext.Provider value={nav}>
        <Suspense fallback={<div className="skeleton" style={{ height: 420, margin: 16 }} />}>
          <ActivityPage id={activityId} />
        </Suspense>
      </NavContext.Provider>
    );
  }

  return (
    <NavContext.Provider value={nav}>
    <RangeProvider>
      <main className="app" inert={notificationsOpen}>
        <AppBar name={session.name} onAlerts={() => setNotificationsOpen(true)} />
        {tab === "today" && <Today name={session.name} />}
        <Suspense fallback={<div className="skeleton" style={{ height: 240, marginTop: 60 }} />}>
          {tab === "workouts" && <Workouts />}
          {tab === "health" && <Health />}
          {tab === "goals" && <Goals />}
        </Suspense>
        {tab === "records" && <Records />}
      </main>
      <TabBar active={tab} onChange={setTab} />
      {notificationsOpen && <Notifications onNavigate={fromNotification} />}
    </RangeProvider>
    </NavContext.Provider>
  );
}
