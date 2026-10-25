import { HistoryBanner } from "../components/HistoryBanner";
import { RangeSelector } from "../components/RangeSelector";
import { Card, ChartPlaceholder, PageHeader, Stat } from "../components/ui";
import { SPORTS } from "../sports";

export function Workouts() {
  return (
    <>
      <PageHeader kicker="אימונים ועומס" title="אימונים" subtitle="כל מה שהשעון רשם, בטווח שבחרת." />
      <HistoryBanner />
      <RangeSelector />
      <div className="grid">
        <div className="grid grid-2">
          <Stat label="אימונים" sub="בטווח שנבחר" />
          <Stat label="מרחק" sub='ק"מ' />
          <Stat label="זמן תנועה" sub="שעות" />
          <Stat label="טיפוס" sub="מטר מצטבר" />
        </div>

        <Card title="שעות לפי ענף" caption="עמודה לכל תקופה, צבע לכל ענף. לחיצה על ענף במקרא מסתירה אותו." soon="שלב 4">
          <div className="legend" style={{ marginBottom: 12 }}>
            {SPORTS.map((sport) => (
              <span key={sport.key}>
                <i className="dot" style={{ background: sport.color }} />
                {sport.label}
              </span>
            ))}
          </div>
          <ChartPlaceholder />
        </Card>

        <Card
          title="Fitness מול Fatigue"
          caption="Fitness נבנה לאט (כ-6 שבועות). Fatigue הוא השבוע האחרון, וקופץ מהר."
          soon="שלב 4"
        >
          <ChartPlaceholder />
        </Card>

        <Card title="Form" caption="Fitness פחות Fatigue. מתחת לאפס: סופגים עבודה. אדום: מעבר לקו ה-30-." soon="שלב 4">
          <ChartPlaceholder height={120} />
        </Card>

        <Card title="השנה ביום-יום" caption="כל משבצת היא יום, וככל שהיא כהה יותר, העומס באותו יום גבוה יותר." soon="שלב 4">
          <ChartPlaceholder height={110} />
        </Card>
      </div>
    </>
  );
}
