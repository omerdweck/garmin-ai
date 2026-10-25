import { hapticSelect } from "../telegram";

export type Tab = "today" | "workouts" | "health" | "goals" | "records";

export const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "today", label: "היום", icon: "🏠" },
  { key: "workouts", label: "אימונים", icon: "📅" },
  { key: "health", label: "בריאות", icon: "❤️" },
  { key: "goals", label: "יעדים", icon: "🎯" },
  { key: "records", label: "שיאים", icon: "🏆" },
];

export function TabBar({ active, onChange }: { active: Tab; onChange(tab: Tab): void }) {
  return (
    <nav className="tabbar" aria-label="לשוניות">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          aria-current={active === tab.key ? "page" : undefined}
          onClick={() => {
            if (tab.key !== active) hapticSelect();
            onChange(tab.key);
          }}
        >
          <span className="icon" aria-hidden="true">
            {tab.icon}
          </span>
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
