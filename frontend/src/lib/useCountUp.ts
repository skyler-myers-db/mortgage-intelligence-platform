import { useEffect, useRef, useState } from 'react';

/**
 * useCountUp — animate a numeric value from 0 up to `target` on mount
 * (and whenever `target` changes). Uses requestAnimationFrame with the
 * prototype's ease curve (cubic-bezier-ish via easeOutCubic) and respects
 * `prefers-reduced-motion` — when the OS flag is set, returns `target`
 * immediately.
 *
 * Durations default to the prototype's --dur-slow token (360ms) but callers
 * can override. Returns the live numeric value — callers format (currency,
 * percent, locale) in their own component so KpiCard's external API stays
 * flexible.
 */
export function useCountUp(target: number, durationMs = 900): number {
  const [value, setValue] = useState<number>(() => (prefersReducedMotion() ? target : 0));
  const rafRef = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }

    // Reset for new target
    startRef.current = null;
    const from = 0;
    const to = target;

    const tick = (now: number) => {
      if (startRef.current === null) startRef.current = now;
      const elapsed = now - startRef.current;
      const t = Math.min(1, elapsed / durationMs);
      // easeOutCubic — similar shape to cubic-bezier(0.2, 0.8, 0.2, 1)
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (to - from) * eased);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setValue(to);
      }
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, durationMs]);

  return value;
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
