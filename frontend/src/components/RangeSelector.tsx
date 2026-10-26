import { RANGE_LABELS, useRange, type RangeKind } from "../range";
import { hapticSelect } from "../telegram";
import { Icon } from "./icons";

/**
 * A pill switch for week / month / year, and under it the period with
 * round arrows. In a right-to-left page the "older" arrow sits on the
 * right, where reading starts - the same way the bot's weekly schedule pages.
 */
export function RangeSelector() {
  const range = useRange();
  const kinds: RangeKind[] = ["week", "month", "year"];

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
      <div className="range-nav">
        <button className="arrow" onClick={range.back} disabled={!range.canGoBack} aria-label="התקופה הקודמת">
          <Icon name="chevronRight" size={20} />
        </button>
        <div className="range-label">{range.label}</div>
        <button className="arrow" onClick={range.forward} disabled={!range.canGoForward} aria-label="התקופה הבאה">
          <Icon name="chevronLeft" size={20} />
        </button>
      </div>
    </>
  );
}
