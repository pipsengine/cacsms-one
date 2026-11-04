import {useEffect, useRef} from 'react';

export const LIVE_MS = 10000;

/** Call the latest loader on a fixed timer. The timer does not reset when the loader identity changes. */
export function useLiveReload(load) {
  const saved = useRef(load);
  saved.current = load;
  useEffect(() => {
    const timer = setInterval(() => {
      try {
        const result = saved.current();
        if (result && typeof result.catch === 'function') result.catch(() => {});
      } catch {
        /* The page's own loader records the error. */
      }
    }, LIVE_MS);
    return () => clearInterval(timer);
  }, []);
}
