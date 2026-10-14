/**
 * The route-nav dock policy (report 12.4 #5, ruled 2026-09-30; D-theme-nav-b).
 *
 * `.route-nav` is sticky at the top of `.main`, the app's scroller, only
 * while its MEASURED block size is at most one sixth of `.main`'s content
 * box (useRouteNavDock writes `data-docked` and `--route-nav-block`). The
 * one-line nav is 57px, so it docks from a 342px scrollport; wrapped to two
 * lines (97px) from 582px, to three (137px) from 822px. A 1366x768 laptop's
 * browser viewport (about 620-660px, a 564-604px scrollport) keeps the
 * docked one-line nav, which the fixed 40rem height query it replaces took
 * away.
 *
 * Why a share: at 400% zoom (1440x900 is then 360x225) the wrapped, sticky
 * nav covered the whole scrollport, so no heading or content could be
 * scrolled into view (WCAG 1.4.10 Reflow; wave-1c follow-up #14). A sticky
 * bar is also what can hide a focused control (WCAG 2.2 SC 2.4.11), so the
 * same measurement drives the focus clearance (38-focus-clearance.css reads
 * `--route-nav-block` while the nav is docked), and a wrapped, docked nav is
 * now cleared by its real height.
 *
 * Fails safe: without the hook (no `.main` ancestor, a test DOM, JS not yet
 * run) the nav carries no `data-docked` and stays in flow.
 */

/** The largest share of `.main`'s content box the docked nav may take. */
export const ROUTE_NAV_MAX_SCROLLPORT_SHARE = 1 / 6;

/** The nav's measured border-box block size, written on `.main` (registered `<length>`, not inherited). */
export const ROUTE_NAV_BLOCK_PROPERTY = '--route-nav-block';

/** Present on `.route-nav` while it is docked (sticky). */
export const ROUTE_NAV_DOCKED_ATTRIBUTE = 'data-docked';

/** Whether a nav of `navBlock` px docks in a scrollport (content box) of `scrollportBlock` px. */
export function routeNavDocks(navBlock: number, scrollportBlock: number): boolean {
  return navBlock > 0 && scrollportBlock > 0 && navBlock <= scrollportBlock * ROUTE_NAV_MAX_SCROLLPORT_SHARE;
}
