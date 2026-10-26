import { useEffect, useRef, useState } from "react";
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

/**
 * Pull-to-refresh and "try again": everything on screen that fetched
 * something registers here, and refreshAll() asks all of it again at
 * once. A registry rather than a key passed down to every screen, so a
 * card added later is refreshed without anyone remembering to wire it.
 */
type Refetch = () => Promise<unknown>;
const refetchers = new Set<Refetch>();

/** Register a refetch for refreshAll(); returns the unregister function (an effect's cleanup). */
export function onRefresh(refetch: Refetch): () => void {
  refetchers.add(refetch);
  return () => {
    refetchers.delete(refetch);
  };
}

/** Refetches everything mounted; resolves once every request has settled. */
export function refreshAll(): Promise<void> {
  recent.clear(); // a refresh that got the cached answer back would refresh nothing
  return Promise.allSettled([...refetchers].map((refetch) => refetch())).then(() => undefined);
}

/** GETs a path; refetches whenever it changes. data stays null while loading. */
export function useApi<T>(path: string): { data: T | null; error: boolean } {
  const [state, setState] = useState<{ path: string; data: T | null; error: boolean }>({
    path,
    data: null,
    error: false,
  });
  const current = useRef(path);
  current.current = path;

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

  // A refresh keeps the numbers on screen until the new ones arrive (no
  // flash of skeletons under the finger), and a failed one keeps them too:
  // slightly old data beats an error where a moment ago there were numbers.
  useEffect(
    () =>
      onRefresh(() => {
        const asked = current.current;
        return shared<T>(asked)
          .then((data) => {
            if (current.current === asked) setState({ path: asked, data, error: false });
          })
          .catch(() => {
            if (current.current === asked) setState((s) => (s.data ? s : { path: asked, data: null, error: true }));
          });
      }),
    [],
  );

  // Never show the previous period's data under the new period's label.
  return state.path === path ? state : { data: null, error: false };
}
