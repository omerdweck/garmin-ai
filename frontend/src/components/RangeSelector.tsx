import { useEffect, useRef, useState } from "react";
import { RANGE_LABELS, useRange, type RangeKind } from "../range";
import { hapticSelect } from "../telegram";
import { Icon } from "./icons";

/**
 * True once the period bar has stuck to the top of the screen: a
 * zero-height marker sits just above it, and the bar is stuck exactly
 * when that marker has scrolled out past the top.
 */
function useStuck() {
  const marker = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const el = marker.current;
    if (!el || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      ([entry]) => setStuck(!entry.isIntersecting && entry.boundingClientRect.top < 40),
      { rootMargin: "-12px 0px 0px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { marker, stuck };
}

/**
 * A pill switch for week / month / year, and under it the period with
 * round arrows. In a right-to-left page the "older" arrow sits on the
 * right, where reading starts - the same way the bot's weekly schedule pages.
 * The period bar stays at the top while the tab scrolls (polish.css), so
 * the last chart on the page is still one tap from the previous week.
 */
export function RangeSelector() {
  const range = useRange();
  const kinds: RangeKind[] = ["week", "month", "year"];
  const { marker, stuck } = useStuck();
  const step = (go: () => void) => () => {
    hapticSelect();
    go();
  };

  return (
    <>
      <div className="segmented" role="group" aria-label="טווח">
        {kinds.map((kind) => (
          <button
            key={kind}
            aria-pressed={range.kind === kind}
            onClick={() => {
              if (kind !== range.kind) hapticSelect();
              range.setKind(kind);
            }}
          >
            {RANGE_LABELS[kind]}
          </button>
        ))}
      </div>
      <div ref={marker} className="range-sentinel" aria-hidden="true" />
      <div className={`range-nav${stuck ? " stuck" : ""}`}>
        <button className="arrow" onClick={step(range.back)} disabled={!range.canGoBack} aria-label="התקופה הקודמת">
          <Icon name="chevronRight" size={20} />
        </button>
        <div className="range-label">{range.label}</div>
        <button className="arrow" onClick={step(range.forward)} disabled={!range.canGoForward} aria-label="התקופה הבאה">
          <Icon name="chevronLeft" size={20} />
        </button>
      </div>
    </>
  );
}
