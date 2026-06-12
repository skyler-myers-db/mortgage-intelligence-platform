import { useEffect, useRef, useState } from 'react';

/**
 * useFirstAppearance — returns true the FIRST time a given key is rendered
 * in this session, false on every subsequent render (including route
 * re-entry). Backs the sleek one-time KPI entrance (re-audit #4 #2).
 *
 * Why this exists: the KPI count-up was removed because it re-ran on every
 * route navigation and read as a "demo-ticker." The fix is not "no motion"
 * — it's "motion exactly once." A module-level set remembers which keys
 * have animated, so re-mounting a KPI (navigating away and back) does NOT
 * replay the entrance. `prefers-reduced-motion` is honored by the consumer
 * (the entrance is CSS; reduced-motion users get the settled state with no
 * animation), but we still mark the key seen so behavior is consistent.
 *
 * The set is module-scoped (per page load), intentionally NOT persisted —
 * a fresh load (the start of a demo) should animate once; an in-session
 * navigation should not.
 */
const seen = new Set<string>();

export function useFirstAppearance(key: string): boolean {
  // First render decides: if the key was never seen, this is the first
  // appearance. We compute it once per mount via lazy state init.
  const [isFirst] = useState(() => !seen.has(key));
  const markedRef = useRef(false);
  useEffect(() => {
    if (!markedRef.current) {
      markedRef.current = true;
      seen.add(key);
    }
  }, [key]);
  return isFirst;
}

/** Test-only: reset the session set so each test starts clean. */
export function __resetFirstAppearanceForTests(): void {
  seen.clear();
}
