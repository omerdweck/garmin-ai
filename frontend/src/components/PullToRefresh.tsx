import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { refreshAll } from "../hooks/useApi";
import { reducedMotion } from "../hooks/useCountUp";
import { hapticSelect, tg } from "../telegram";

const START_PX = 10; // finger travel before deciding it's a pull and not a tap or a sideways swipe
const DAMPING = 0.5; // the circle moves half as far as the finger, so it feels attached, not dragged
const THRESHOLD = 64;
const MAX = 96;
const MIN_SPIN_MS = 450; // a refresh answered from a warm server would otherwise just blink

/**
 * Telegram treats a downward swipe at the top of a Mini App as "minimize
 * me" - the same gesture as pulling to refresh. Bot API 7.7 lets the page
 * keep it; the window's own close button and the back button still work.
 */
function keepVerticalSwipes() {
  const app = tg as (typeof tg & { disableVerticalSwipes?: () => void }) | null;
  try {
    if (app?.disableVerticalSwipes && app.isVersionAtLeast("7.7")) app.disableVerticalSwipes();
  } catch {
    /* older clients: pulling may minimize there, nothing worse */
  }
}

/**
 * Pull down from the top of a tab to refetch everything on it.
 *
 * Touch only (a mouse has no "pull"), and it listens on the document
 * instead of wrapping the page, so it needs no change to the screens.
 * It only claims a gesture that starts at the very top and moves mostly
 * down: a sideways drag over a chart (its tooltip, its zoom) is left
 * alone, and so is any gesture once the page has scrolled.
 */
export function PullToRefresh() {
  const [pull, setPull] = useState(0);
  const [busy, setBusy] = useState(false);
  const gesture = useRef<{ x: number; y: number; pulling: boolean } | null>(null);
  const pullRef = useRef(0);
  const busyRef = useRef(false);

  useEffect(() => {
    keepVerticalSwipes();

    const set = (value: number) => {
      if ((pullRef.current < THRESHOLD) !== (value < THRESHOLD) && value >= THRESHOLD) hapticSelect();
      pullRef.current = value;
      setPull(value);
    };

    const onStart = (e: TouchEvent) => {
      if (busyRef.current || e.touches.length !== 1 || window.scrollY > 0) return;
      gesture.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, pulling: false };
    };

    const onMove = (e: TouchEvent) => {
      const g = gesture.current;
      if (!g) return;
      const dx = e.touches[0].clientX - g.x;
      const dy = e.touches[0].clientY - g.y;
      if (!g.pulling) {
        if (Math.abs(dx) > START_PX && Math.abs(dx) > Math.abs(dy)) gesture.current = null; // sideways: not ours
        else if (dy < -START_PX || window.scrollY > 0) gesture.current = null; // scrolling the page
        else if (dy > START_PX) g.pulling = true;
        if (!g.pulling) return;
      }
      set(Math.max(0, Math.min(MAX, (dy - START_PX) * DAMPING)));
    };

    const onEnd = () => {
      const g = gesture.current;
      gesture.current = null;
      if (!g?.pulling) return;
      if (pullRef.current < THRESHOLD) {
        set(0);
        return;
      }
      busyRef.current = true;
      setBusy(true);
      set(THRESHOLD);
      const started = Date.now();
      refreshAll().then(() => {
        window.setTimeout(() => {
          busyRef.current = false;
          setBusy(false);
          set(0);
        }, Math.max(0, MIN_SPIN_MS - (Date.now() - started)));
      });
    };

    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchmove", onMove, { passive: true });
    document.addEventListener("touchend", onEnd);
    document.addEventListener("touchcancel", onEnd);
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onEnd);
    };
  }, []);

  if (pull === 0 && !busy) return null;
  const still = reducedMotion();
  const ready = pull >= THRESHOLD;
  return createPortal(
    <div
      className={`ptr${busy ? " busy" : ""}${ready ? " ready" : ""}${gesture.current ? " dragging" : ""}`}
      style={{ transform: `translate(-50%, ${pull - 44}px)`, opacity: Math.min(1, pull / THRESHOLD) }}
      role="status"
      aria-label={busy ? "מרענן" : ready ? "שחרר לרענון" : "משוך לרענון"}
    >
      <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
        style={busy || still ? undefined : { transform: `rotate(${(pull / THRESHOLD) * 270}deg)` }}>
        <path d="M21 12a9 9 0 1 1-2.64-6.36L21 8 M21 3v5h-5" />
      </svg>
    </div>,
    document.body,
  );
}
