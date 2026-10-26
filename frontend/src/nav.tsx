/**
 * Opening one workout from anywhere in the app (a list, a chart, a link
 * from the bot's post-workout message) without a router: the open workout
 * is one piece of state in App, handed down through this context.
 */

import { createContext, useContext } from "react";

export const OpenActivityContext = createContext<(id: number) => void>(() => {});

export function useOpenActivity(): (id: number) => void {
  return useContext(OpenActivityContext);
}

/** ?activity=123 on the Mini App's URL - set by the button under a post-workout message. */
export function activityFromUrl(): number | null {
  const value = new URLSearchParams(window.location.search).get("activity");
  const id = value ? Number(value) : NaN;
  return Number.isInteger(id) && id > 0 ? id : null;
}
