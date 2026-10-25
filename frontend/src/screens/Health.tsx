import { HistoryBanner } from "../components/HistoryBanner";
import { RangeSelector } from "../components/RangeSelector";
import { Card, ChartPlaceholder, PageHeader } from "../components/ui";

// The explanation line of each metric card, as in the plan (part C4).
const METRICS = [
  { name: "דופק מנוחה", caption: "ממוצע 30 הדקות הנמוכות בכל יום." },
  { name: "HRV", caption: "השונות בין פעימות בשינה. גבוה = התאוששות טובה." },
  { name: "Stress", caption: "ציון 0–100 של Garmin, מ-HRV בזמן מנוחה." },
  { name: "שיא Body Battery", caption: "הטעינה הגבוהה ביותר בכל יום." },
  { name: "דקות עצימות", caption: "מתונה, ועצימה כפול 2." },
  { name: "נשימה", caption: "נשימות לדקה בממוצע, בשינה." },
];

export function Health() {
  return (
    <>
      <PageHeader kicker="בריאות ושינה" title="כל יום, כל היום" subtitle="הסיכום היומי של השעון, יום אחרי יום." />
      <HistoryBanner />
      <RangeSelector />
      <div className="grid">
        {METRICS.map((metric) => (
          <Card key={metric.name} title={metric.name} caption={metric.caption}>
            <ChartPlaceholder height={90} />
          </Card>
        ))}
        <Card title="שינה" caption="שעות לפי שלבים בכל לילה, וקו ציון השינה." soon="שלב 6">
          <ChartPlaceholder />
        </Card>
      </div>
    </>
  );
}
