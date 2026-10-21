import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** `useState` for a string filter that lives in the URL query (`?q=…`), so
 *  filtered views survive reloads and can be shared. Values equal to the
 *  default are dropped from the URL; updates replace the history entry
 *  instead of adding one per keystroke. */
export function useUrlState<T extends string>(key: string, defaultValue: T): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams();
  const value = (params.get(key) ?? defaultValue) as T;
  const setValue = useCallback(
    (next: T) => {
      setParams(
        (prev) => {
          const updated = new URLSearchParams(prev);
          if (!next || next === defaultValue) updated.delete(key);
          else updated.set(key, next);
          return updated;
        },
        { replace: true },
      );
    },
    [key, defaultValue, setParams],
  );
  return [value, setValue];
}
