/** Number formats shared by the workout list and page. */

export const fmt = (n: number, digits = 0) =>
  n.toLocaleString("he-IL", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** 3725 -> "1:02:05", 305 -> "5:05". */
export function duration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

/** Metres per second to a pace per km ("5:05") - or per 100 m for swimming. */
export function pace(metersPerSecond: number | null | undefined, per = 1000): string {
  if (!metersPerSecond || metersPerSecond <= 0.2) return "—";
  return duration(per / metersPerSecond);
}

export const distance = (meters: number, sport: string) =>
  sport === "swimming" ? `${fmt(meters)} מ׳` : `${fmt(meters / 1000, meters < 10000 ? 2 : 1)} ק״מ`;

export function when(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("he-IL", { weekday: "short", day: "numeric", month: "numeric" }) +
    " · " + d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
}
