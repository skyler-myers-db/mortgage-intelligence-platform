import { useEffect, useState } from 'react';

/**
 * usePrefersReducedMotion — watches the `(prefers-reduced-motion: reduce)`
 * media query and returns `true` when the user has opted out of motion.
 *
 * Why this isn't just a CSS guard: SVG SMIL `<animate>` elements (used
 * for the Illinois + Cook County beacon pulses in the choropleth map)
 * don't respect the CSS `@media (prefers-reduced-motion)` rule the way
 * CSS transitions / keyframes do. The only reliable way to suppress
 * them is to conditionally render the `<animate>` child, which requires
 * reading the preference at JS runtime.
 *
 * Hole-finder finding #18, 2026-04-23 audit.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false;
    }
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    // `addEventListener('change', ...)` is the modern API; older
    // Safari uses `addListener`. Both are feature-detected to avoid
    // breaking in legacy browsers.
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    mq.addListener(onChange);
    return () => mq.removeListener(onChange);
  }, []);

  return reduced;
}
