import { useEffect, useRef, type RefObject } from 'react';

interface RevealOnScrollOptions {
  /**
   * When true the element is treated as already revealed: no
   * IntersectionObserver is attached and the caller renders `.is-visible`
   * from the first paint (audit motion-06: a reveal is once per session per
   * key, so later mounts must not replay the fade).
   */
  initiallyVisible?: boolean;
}

/**
 * useRevealOnScroll — toggles `.is-visible` on the target element the first
 * time it crosses the viewport threshold, then disconnects. One-shot reveal
 * used for below-fold sections (home brand signature, trigger timelines,
 * alternatives/thresholds row). Respects `prefers-reduced-motion: reduce`:
 * under that flag the element is marked visible immediately so nothing is
 * gated behind scroll. Pair with `useFirstAppearance` (see <Reveal/>) so the
 * fade plays once per session per key rather than on every mount.
 */
export function useRevealOnScroll<T extends HTMLElement>(
  { initiallyVisible = false }: RevealOnScrollOptions = {},
): RefObject<T | null> {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Already revealed earlier this session: the caller rendered the final
    // state, so nothing to observe and no transition to run.
    if (initiallyVisible) {
      el.classList.add('is-visible');
      return;
    }

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
  }, [initiallyVisible]);

  return ref;
}
