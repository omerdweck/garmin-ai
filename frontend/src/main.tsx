import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { tg } from "./telegram";
import "./theme.css";

/**
 * Light or dark, from Telegram's own setting - so the Mini App opens in
 * the same mode as the chat behind it, and follows when the user switches.
 * Telegram's header and background are painted in our page color, so the
 * window has no seam where Telegram's frame meets the page.
 */
function applyTheme() {
  const dark = tg ? tg.colorScheme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (tg) {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    try {
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
    } catch {
      /* clients older than Bot API 6.1 */
    }
  }
}

applyTheme();
if (tg) {
  tg.onEvent("themeChanged", applyTheme);
  tg.ready();
  tg.expand();
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
