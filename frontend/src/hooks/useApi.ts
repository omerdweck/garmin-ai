import { useEffect, useState } from "react";
import { apiGet } from "../api";

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
    apiGet<T>(path)
      .then((data) => alive && setState({ path, data, error: false }))
      .catch(() => alive && setState({ path, data: null, error: true }));
    return () => {
      alive = false;
    };
  }, [path]);

  // Never show the previous period's data under the new period's label.
  return state.path === path ? state : { data: null, error: false };
}
