/**
 * The user's reduced-motion preference, read at the moment of a scroll or an
 * entrance. Its own module so the Genie answer chunk (shared by the panel and
 * /ask-genie) does not pull in the panel-only transcript scroll hook.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
