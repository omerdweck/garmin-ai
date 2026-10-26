import { Card, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { LoadDay } from "../charts/workoutCharts";

const RINGS = [
  { label: "Body Battery", color: "var(--sport-swimming)" },
  { label: "HRV", color: "var(--sport-running)" },
  { label: "שינה", color: "var(--sport-other)" },
  { label: "דופק מנוחה", color: "var(--sport-cycling)" },
];

function greeting(hour: number): string {
  if (hour < 5) return "לילה טוב";
  if (hour < 12) return "בוקר טוב";
  if (hour < 17) return "צהריים טובים";
  if (hour < 21) return "ערב טוב";
  return "לילה טוב";
}

/** An empty ring for now; step 5 fills it against the personal baseline. */
function Ring({ label, color }: { label: string; color: string }) {
  return (
    <div style={{ textAlign: "center" }}>
      <svg viewBox="0 0 44 44" width="64" height="64" aria-hidden="true">
        <circle cx="22" cy="22" r="18" fill="none" stroke="var(--skeleton)" strokeWidth="5" />
        <circle
          cx="22" cy="22" r="18" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray="113" strokeDashoffset="113" transform="rotate(-90 22 22)" opacity="0.9"
        />
      </svg>
      <div className="stat-sub">{label}</div>
    </div>
  );
}

/** Today's Form, in words as well as a number. Thresholds match the Form chart. */
function formWords(value: number): { text: string; color: string } {
  if (value >= 5) return { text: "רענן. גוף מוכן לאימון קשה", color: "var(--sport-swimming)" };
  if (value >= -10) return { text: "מאוזן", color: "var(--text)" };
  if (value >= -30) return { text: "סופג עבודה. אזור האימון הפרודוקטיבי", color: "var(--accent)" };
  return { text: "עומס גבוה. שווה יום קל", color: "var(--zone-5)" };
}

function FormCard() {
  const { data } = useApi<{ days: LoadDay[] }>("/api/load?kind=week&offset=0");
  const today = data?.days.at(-1);
  const words = today ? formWords(today.form) : null;
  return (
    <Card kicker="Form היום" caption="Fitness פחות Fatigue. מעל אפס: רענן. מתחת ל-30-: עומס גבוה.">
      {today && words ? (
        <>
          <div className="stat-value num" style={{ color: words.color }}>
            {today.form > 0 ? "+" : ""}
            {today.form.toFixed(0)}
          </div>
          <div className="stat-sub">{words.text}</div>
        </>
      ) : data ? (
        <div className="stat-sub">אין עדיין אימונים עם עומס.</div>
      ) : (
        <div className="skeleton" style={{ height: 32, width: "40%" }} />
      )}
    </Card>
  );
}

export function Today({ name }: { name: string }) {
  const now = new Date();
  const date = now.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });
  return (
    <>
      <PageHeader kicker={date} title={`${greeting(now.getHours())}${name ? `, ${name}` : ""}`} />
      <div className="grid">
        <FormCard />
        <Card kicker="מוכנות להיום" caption="כל טבעת מראה איפה אתה היום מול הנורמה האישית שלך, לא מול ממוצע כללי." soon="שלב 5">
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            {RINGS.map((ring) => (
              <Ring key={ring.label} {...ring} />
            ))}
          </div>
        </Card>
        <Card kicker="האימון של היום" caption="המבנה של האימון המתוכנן, בלוק אחרי בלוק, צבוע לפי עוצמה." soon="שלב 5" />
      </div>
    </>
  );
}
