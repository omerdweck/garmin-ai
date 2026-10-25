import { Card, PageHeader } from "../components/ui";

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

export function Today({ name }: { name: string }) {
  const now = new Date();
  const date = now.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });
  return (
    <>
      <PageHeader kicker={date} title={`${greeting(now.getHours())}${name ? `, ${name}` : ""}`} />
      <div className="grid">
        <Card kicker="מוכנות להיום" caption="כל טבעת מראה איפה אתה היום מול הנורמה האישית שלך, לא מול ממוצע כללי." soon="שלב 5">
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            {RINGS.map((ring) => (
              <Ring key={ring.label} {...ring} />
            ))}
          </div>
        </Card>
        <Card kicker="האימון של היום" caption="המבנה של האימון המתוכנן, בלוק אחרי בלוק, צבוע לפי עוצמה." soon="שלב 5" />
        <Card kicker="Form" caption="Fitness פחות Fatigue. מעל אפס: רענן. מתחת ל-30-: עומס גבוה." soon="שלב 4" />
      </div>
    </>
  );
}
