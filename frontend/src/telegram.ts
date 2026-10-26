/**
 * The small part of Telegram's WebApp object this app uses, typed.
 *
 * Telegram injects window.Telegram.WebApp when the page is opened from the
 * bot. Opened anywhere else (a normal browser, or `npm run dev`) it is
 * missing - every caller has to handle `tg === null`.
 */

type Haptic = {
  selectionChanged(): void;
  impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
};

type BackButton = {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
};

export type TelegramWebApp = {
  initData: string;
  initDataUnsafe: { start_param?: string; user?: { first_name?: string; photo_url?: string } };
  colorScheme: "light" | "dark";
  version: string;
  ready(): void;
  expand(): void;
  close(): void;
  isVersionAtLeast(version: string): boolean;
  setHeaderColor(color: string): void;
  setBackgroundColor(color: string): void;
  onEvent(event: "themeChanged", cb: () => void): void;
  offEvent(event: "themeChanged", cb: () => void): void;
  BackButton: BackButton;
  HapticFeedback: Haptic;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

const webApp = window.Telegram?.WebApp;

/** Null outside Telegram; initData is empty when the script loaded but no bot opened the page. */
export const tg: TelegramWebApp | null = webApp && webApp.initData ? webApp : null;

/** A short tap-feel on supporting phones; silently nothing elsewhere. */
export function hapticSelect(): void {
  try {
    tg?.HapticFeedback.selectionChanged();
  } catch {
    /* older clients */
  }
}
