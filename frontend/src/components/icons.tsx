/**
 * Line icons in the mockup's style: 24px grid, round caps, drawn with
 * currentColor so they follow the text color in both themes.
 */

const PATHS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  pulse: "M22 12h-4l-3 9L9 3l-3 9H2",
  heart: "M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z",
  target: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12Z M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  trophy: "M8 21h8 M12 17v4 M7 4h10v5a5 5 0 0 1-10 0V4Z M17 5h3v1.5A3.5 3.5 0 0 1 16.5 10 M7 5H4v1.5A3.5 3.5 0 0 0 7.5 10",
  bell: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9 M10.3 21a1.94 1.94 0 0 0 3.4 0",
  bars: "M5 20v-6 M12 20V4 M19 20v-10",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z M12 7v5l3 2",
  flame: "M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5Z",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z M18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z M8 17h7.5a3.5 3.5 0 0 0 0-7h-7a3.5 3.5 0 0 1 0-7H16",
  mountain: "m8 3 4 8 5-5 5 15H2L8 3Z",
  check: "M20 6 9 17l-5-5",
  chevronLeft: "m15 18-6-6 6-6",
  chevronRight: "m9 18 6-6-6-6",
  moon: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z",
  zap: "M13 2 3 14h9l-1 8 10-12h-9l1-8Z",
  calendar: "M8 2v4 M16 2v4 M3 10h18 M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
  watch: "M12 9v3l1.5 1.5 M16.5 7.5 16 2H8l-.5 5.5 M7.5 16.5 8 22h8l.5-5.5 M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12Z",
  alert: "M12 9v4 M12 17h.01 M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z",
  medal: "M7.21 15 2.66 7.14a2 2 0 0 1 .13-2.2L4.4 2.8A2 2 0 0 1 6 2h12a2 2 0 0 1 1.6.8l1.6 2.14a2 2 0 0 1 .14 2.2L16.79 15 M11 12 5.12 2.2 M13 12l5.88-9.8 M12 22a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z",
  lungs: "M12 4v8 M12 12c-1.5 0-3-1-3.5-2.5C7.5 6 4 7 3.5 11c-.4 3.6-.5 7 1.5 8.5 2 1.5 5 0 5.5-2.5.3-1.6.5-3.5 1.5-5Z M12 12c1.5 0 3-1 3.5-2.5C16.5 6 20 7 20.5 11c.4 3.6.5 7-1.5 8.5-2 1.5-5 0-5.5-2.5-.3-1.6-.5-3.5-1.5-5Z",
  drop: "M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5S12.5 5 12 2.5c-.5 2.5-2 4.9-4 6.5S5 13 5 15a7 7 0 0 0 7 7Z",
  battery: "M16 7h1a2 2 0 0 1 2 2v.5h1.5v5H19v.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h11Z M7 10v4 M11 10v4",
  gauge: "m12 14 4-4 M3.34 19a10 10 0 1 1 17.32 0",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 22, stroke = 1.8, className }: { name: IconName; size?: number; stroke?: number; className?: string }) {
  return (
    <svg
      className={`icon-svg${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
