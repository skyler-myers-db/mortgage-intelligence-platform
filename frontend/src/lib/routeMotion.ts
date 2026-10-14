/**
 * The painted route's motion contract, shared by app.tsx's RouteTransition
 * and the Lead Queue keep-alive slot (routes/lead-queue.keepAlive.tsx).
 *
 * The View Transition classes of the painted route (app.transitions.css):
 * `::view-transition-old(.mip-route-exit)` / `::view-transition-new(.mip-route-enter)`.
 */
export const ROUTE_EXIT_CLASS = 'mip-route-exit';
export const ROUTE_ENTER_CLASS = 'mip-route-enter';

/** Read ONCE per boundary, at mount (see app.tsx RouteTransition). */
export function prefersReducedMotionAtMount(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
