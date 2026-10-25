import { Card, PageHeader } from "../components/ui";

export function Goals() {
  return (
    <>
      <PageHeader kicker="יעדים ותוכנית" title="יעדים" subtitle="לאן אתה הולך, ואיך אתה מתקדם לשם." />
      <div className="grid">
        <Card
          title="התקדמות ליעד"
          caption="תחזית Garmin לאורך זמן, מול הקו הנדרש כדי להגיע ליעד בתאריך."
          soon="אחרי התוכנית ליעד במאמן"
        />
        <Card title="השבוע המתוכנן" caption="ראשון עד שבת, מתוכנן מול מה שבוצע בפועל." soon="שלב 9" />
      </div>
    </>
  );
}
