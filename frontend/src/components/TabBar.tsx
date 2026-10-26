import { hapticSelect } from "../telegram";
import { Icon, type IconName } from "./icons";

export type Tab = "today" | "workouts" | "health" | "goals" | "records";

export const TABS: { key: Tab; label: string; icon: IconName }[] = [
  { key: "today", label: "היום", icon: "home" },
  { key: "workouts", label: "אימונים", icon: "pulse" },
  { key: "health", label: "בריאות", icon: "heart" },
  { key: "goals", label: "יעדים", icon: "target" },
  { key: "records", label: "שיאים", icon: "trophy" },
];

export function TabBar({ active, onChange }: { active: Tab; onChange(tab: Tab): void }) {
  return (
    <nav className="tabbar" aria-label="לשוניות">
      {TABS.map((tab) => {
        const on = active === tab.key;
        return (
          <button
            key={tab.key}
            aria-current={on ? "page" : undefined}
            onClick={() => {
              if (!on) hapticSelect();
              onChange(tab.key);
            }}
          >
            <Icon name={tab.icon} size={24} stroke={on ? 2.4 : 1.7} />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}
