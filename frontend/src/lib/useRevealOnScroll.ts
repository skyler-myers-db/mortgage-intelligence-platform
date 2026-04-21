import { useEffect, useRef, type RefObject } from 'react';

/**
 * useRevealOnScroll — toggles `.is-visible` on the target element the first
 * time it crosses the viewport threshold, then disconnects. One-shot reveal
 * used for below-fold sections (home future-modules, trigger timelines,
 * alternatives/thresholds row). Respects `prefers-reduced-motion: reduce`:
 * under that flag the element is marked visible immediately so nothing is
 * gated behind scroll.
 */
export function useRevealOnScroll<T extends HTMLElement>(): RefObject<T | null> {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Reduced-motion or SSR fallback — reveal immediately.
    if (
      typeof window === 'undefined' ||
      typeof IntersectionObserver === 'undefined' ||
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    ) {
      el.classList.add('is-visible');
      return;
    }

    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            (entry.target as HTMLElement).classList.add('is-visible');
            obs.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' },
    );

    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return ref;
}
