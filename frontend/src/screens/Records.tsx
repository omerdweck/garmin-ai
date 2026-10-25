import { Card, PageHeader } from "../components/ui";

export function Records() {
  return (
    <>
      <PageHeader kicker="שיאים והישגים" title="שיאים" subtitle="הטובים ביותר שלך, בכל ענף." />
      <div className="grid">
        <Card title="קיר ההישגים" caption="כל מה שכבר הושג, וכמה נשאר לבא בתור." soon="שלב 9" />
        <Card title="שיאים אישיים" caption='1, 5 ו-10 ק"מ, חצי מרתון, 100 ו-400 מ׳ שחייה, ו-1RM לכל תרגיל.' soon="שלב 9" />
        <Card title="השנה שלי" caption="ק״מ, שעות, הענף המוביל והחודש החזק ביותר." soon="שלב 9" />
      </div>
    </>
  );
}
