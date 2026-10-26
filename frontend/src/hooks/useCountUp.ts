import { useEffect, useRef, type RefObject } from "react";

/** The phone's "reduce motion" setting; read each time, it can change while the app is open. */
export function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

const DURATION_MS = 600;
// "1,234", "12.5", "+7", "-3" - and nothing else: a pace ("5:32"), a range
// ("48–56") or a dash is text, and counting it up would only garble it.
const NUMBER = /^([+-]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?$/;

type Run = { target: string; written: string; frame: number };
const runs = new WeakMap<Text, Run>();
const shown = new WeakMap<Text, number>(); // the last number each text node settled on

function format(value: number, like: RegExpExecArray): string {
  const digits = like[3]?.length ?? 0;
  const body = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: like[2].includes(","),
  });
  return `${like[1]}${body}`;
}

function animate(node: Text, target: string) {
  const match = NUMBER.exec(target)!;
  const to = Number(`${match[1]}${match[2].replaceAll(",", "")}${match[3] ? `.${match[3]}` : ""}`);
  const from = shown.get(node) ?? 0;
  shown.set(node, to);
  if (from === to) return;
  const start = performance.now();
  const run: Run = { target, written: target, frame: 0 };
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / DURATION_MS);
    const eased = 1 - (1 - t) ** 3; // fast start, gentle landing
    // Always end on React's own string, so the DOM matches what React thinks it rendered.
    run.written = t < 1 ? format(from + (to - from) * eased, match) : target;
    node.nodeValue = run.written;
    if (t < 1) run.frame = requestAnimationFrame(step);
    else runs.delete(node);
  };
  runs.set(node, run);
  run.frame = requestAnimationFrame(step);
}

/**
 * Big numbers count up from zero the first time they appear, and from the
 * old value when they change (a new week picked) - once per value, about
 * half a second. Works on whatever text is inside `ref`, so a number
 * passed as a ReactNode (the hero card's stats) counts up as well.
 *
 * It writes the text nodes directly rather than through state: a number
 * re-rendered 36 times would re-render its whole card 36 times. React
 * only rewrites a text node when its own value changes, and every run
 * ends on exactly that value, so the two never disagree for long.
 */
export function useCountUp<T extends HTMLElement>(): RefObject<T | null> {
  const ref = useRef<T>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root || reducedMotion()) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
      const text = node.nodeValue ?? ""; // untrimmed on purpose: a run ends by writing exactly this
      const run = runs.get(node);
      if (run) {
        if (text === run.written || text === run.target) continue; // our own frame, or nothing new
        cancelAnimationFrame(run.frame); // React wrote a new value mid-run
        runs.delete(node);
      }
      if (!NUMBER.test(text)) continue;
      const settled = shown.get(node);
      if (settled !== undefined && format(settled, NUMBER.exec(text)!) === text) continue;
      animate(node, text);
    }
  });

  return ref;
}
