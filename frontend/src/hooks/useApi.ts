import { useEffect, useState } from "react";
import { apiGet } from "../api";

/**
 * Two parts of a screen asking for the same path at the same moment (the
 * top bar and the home tab both read /api/today) share one request. Kept
 * for a few seconds only: switching tabs still brings fresh numbers.
 */
const SHARE_MS = 5_000;
const recent = new Map<string, { at: number; promise: Promise<unknown> }>();

function shared<T>(path: string): Promise<T> {
  const hit = recent.get(path);
  if (hit && Date.now() - hit.at < SHARE_MS) return hit.promise as Promise<T>;
  const promise = apiGet<T>(path);
  recent.set(path, { at: Date.now(), promise });
  promise.catch(() => recent.delete(path));
  return promise;
}

/** GETs a path; refetches whenever it changes. data stays null while loading. */
export function useApi<T>(path: string): { data: T | null; error: boolean } {
  const [state, setState] = useState<{ path: string; data: T | null; error: boolean }>({
    path,
    data: null,
    error: false,
  });

  useEffect(() => {
    let alive = true;
    setState({ path, data: null, error: false });
    shared<T>(path)
      .then((data) => alive && setState({ path, data, error: false }))
      .catch(() => alive && setState({ path, data: null, error: true }));
    return () => {
      alive = false;
    };
  }, [path]);

  // Never show the previous period's data under the new period's label.
  return state.path === path ? state : { data: null, error: false };
}
