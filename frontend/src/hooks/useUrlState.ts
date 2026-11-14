import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

const URL_WRITE_DELAY_MS = 250;

/** `useState` for a string filter that lives in the URL query (`?q=…`), so
 *  filtered views survive reloads and can be shared. Values equal to the
 *  default are dropped from the URL; updates replace the history entry
 *  instead of adding one per keystroke.
 *
 *  The value itself lives in local state and the URL write is debounced:
 *  round-tripping every keystroke through the router dropped characters when
 *  typing fast (UAT DEF-005). External URL changes (back/forward, links) are
 *  still picked up. */
export function useUrlState<T extends string>(key: string, defaultValue: T): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams();
  const urlValue = (params.get(key) ?? defaultValue) as T;
  const [value, setLocalValue] = useState<T>(urlValue);
  // The last value this hook wrote (or is about to write) to the URL.
  const pendingRef = useRef<T>(urlValue);
  const timerRef = useRef<number | null>(null);

  // Adopt URL changes that did not originate from this hook.
  useEffect(() => {
    if (urlValue !== pendingRef.current) {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
      pendingRef.current = urlValue;
      setLocalValue(urlValue);
    }
  }, [urlValue]);

  useEffect(() => () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
  }, []);

  const setValue = useCallback(
    (next: T) => {
      setLocalValue(next);
      pendingRef.current = (next || defaultValue) as T;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        setParams(
          (prev) => {
            const updated = new URLSearchParams(prev);
            if (!next || next === defaultValue) updated.delete(key);
            else updated.set(key, next);
            return updated;
          },
          { replace: true },
        );
      }, URL_WRITE_DELAY_MS);
    },
    [key, defaultValue, setParams],
  );
  return [value, setValue];
}
