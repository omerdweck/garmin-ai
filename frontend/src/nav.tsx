/**
 * Moving around the app without a router. App owns the state (which tab,
 * which workout is open) and hands these functions down through context,
 * so any card can say "take me there":
 *
 *   const nav = useNav();
 *   nav.openTab("health", "metric-avg_hrv");   // switch tab, then scroll to #metric-avg_hrv
 *   nav.openActivity(123);                      // the single-workout page
 *
 * The focus target is just a DOM id. App waits (up to a few seconds, while
 * the tab's data loads) for an element with that id, scrolls it into view
 * and flashes it briefly (.focus-flash in theme.css).
 */

import { createContext, useContext } from "react";
import type { Tab } from "./components/TabBar";

export type Nav = {
  openTab(tab: Tab, focus?: string): void;
  openActivity(id: number): void;
};

export const NavContext = createContext<Nav>({ openTab: () => {}, openActivity: () => {} });

export function useNav(): Nav {
  return useContext(NavContext);
}

/** Kept for existing callers: open one workout. */
export function useOpenActivity(): (id: number) => void {
  return useContext(NavContext).openActivity;
}

/** ?activity=123 on the Mini App's URL - set by the button under a post-workout message. */
export function activityFromUrl(): number | null {
  const value = new URLSearchParams(window.location.search).get("activity");
  const id = value ? Number(value) : NaN;
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** Scrolls to #id once it exists (the tab's data may still be loading), then flashes it. */
export function scrollToFocus(id: string, timeoutMs = 4000): () => void {
  const started = Date.now();
  let frame = 0;
  const tick = () => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      el.classList.remove("focus-flash");
      void el.offsetWidth; // restart the animation
      el.classList.add("focus-flash");
      return;
    }
    if (Date.now() - started < timeoutMs) frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(frame);
}
