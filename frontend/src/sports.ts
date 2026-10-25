/**
 * One color per sport, used by every chart and screen - so a sport is
 * recognised by color before anyone reads a label. Values match the plan
 * (claude/mini-app-plan.md, part B2) and the CSS tokens in theme.css.
 */

export type Sport = "running" | "cycling" | "swimming" | "strength" | "walking" | "other";

export const SPORTS: { key: Sport; label: string; icon: string; color: string }[] = [
  { key: "running", label: "ריצה", icon: "🏃", color: "var(--sport-running)" },
  { key: "cycling", label: "רכיבה", icon: "🚴", color: "var(--sport-cycling)" },
  { key: "swimming", label: "שחייה", icon: "🏊", color: "var(--sport-swimming)" },
  { key: "strength", label: "כוח", icon: "🏋️", color: "var(--sport-strength)" },
  { key: "walking", label: "הליכה", icon: "🚶", color: "var(--sport-walking)" },
  { key: "other", label: "אחר", icon: "✨", color: "var(--sport-other)" },
];
