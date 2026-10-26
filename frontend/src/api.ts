/**
 * Talking to our API (the webapp service behind Caddy, /api/*).
 *
 * 1. authenticate() sends Telegram's signed initData to /api/auth and gets
 *    back a JWT that is valid for an hour.
 * 2. apiGet() / apiPost() send that token with every request. If it has expired
 *    (401), it asks for a new one once and repeats the request.
 *
 * The token lives only in memory, never in localStorage: closing the Mini
 * App throws it away, and a new launch brings fresh initData anyway.
 */

import { tg } from "./telegram";

export type AuthResult = { first_name: string; garmin_linked: boolean };

/** Telegram vouches for this person, but they aren't an approved user (403). */
export class NotMemberError extends Error {}

let token: string | null = null;

export async function authenticate(): Promise<AuthResult> {
  if (!tg) throw new Error("not inside Telegram");
  const res = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: tg.initData }),
  });
  if (res.status === 403) throw new NotMemberError();
  if (!res.ok) throw new Error(`auth failed: ${res.status}`);
  const body = (await res.json()) as AuthResult & { token: string };
  token = body.token;
  return { first_name: body.first_name, garmin_linked: body.garmin_linked };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const send = () =>
    fetch(path, {
      ...init,
      headers: { ...(init.headers as Record<string, string>), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });

  let res = await send();
  if (res.status === 401 && tg) {
    // The hour is up (or the page slept): one fresh login, one retry.
    await authenticate();
    res = await send();
  }
  if (res.status === 403) throw new NotMemberError();
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return (await res.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

/** The same, with a JSON body - for the few things the page changes (marking notifications read). */
export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
