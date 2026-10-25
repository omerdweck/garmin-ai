import { RANGE_LABELS, useRange, type RangeKind } from "../range";
import { hapticSelect } from "../telegram";

/**
 * In a right-to-left page the "older" arrow sits on the right, where
 * reading starts - the same way the bot's weekly schedule pages.
 */
export function RangeSelector() {
  const range = useRange();
  const kinds: RangeKind[] = ["week", "month", "year"];

  return (
    <>
      <div className="range">
        <button className="arrow" onClick={range.back} disabled={!range.canGoBack} aria-label="התקופה הקודמת">
          ›
        </button>
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
        <button className="arrow" onClick={range.forward} disabled={!range.canGoForward} aria-label="התקופה הבאה">
          ‹
        </button>
      </div>
      <div className="range-label">{range.label}</div>
    </>
  );
}
