// Shell-initial placeholder: no memo caches needed (see DegradedBanner.tsx).
'use no memo';

import { useIsOnline } from '../../lib/connectivity';
import { useShowAfterDelay } from '../ui/useShowAfterDelay';

/**
 * RouteFallback — the route Suspense fallback and the admin gate's
 * session-pending state (app.tsx).
 *
 * Page-shaped (audit 2026-09-21 `states-10`): the PageShell frame a route
 * renders (`.main__content` > `.main__inner` > `.proto-hero`, then a panel)
 * with the panel's height reserved, instead of one short generic card that the
 * real page then pushed down by a thousand pixels.
 *
 * Show-delay: a preloaded route chunk or the session check routinely resolves
 * within a few frames, which used to flash this skeleton for one of them. It
 * is laid out at once (its space stays reserved) but only becomes visible
 * after ROUTE_FALLBACK_DELAY_MS. While the browser is offline it says it is
 * waiting for the connection instead of implying progress.
 */

export const ROUTE_FALLBACK_DELAY_MS = 150;

const HIDDEN = { visibility: 'hidden' } as const;

export function RouteFallback() {
  const shown = useShowAfterDelay(ROUTE_FALLBACK_DELAY_MS);
  const online = useIsOnline();
  return (
    <div
      className="main__content"
      role="status"
      aria-busy={online}
      data-route-fallback=""
      style={shown ? undefined : HIDDEN}
    >
      <div className="main__inner">
        <div className="proto-hero" aria-hidden="true">
          <div>
            <span className="skeleton skeleton--eyebrow" />
            <span className="skeleton skeleton--title" />
            <span className="skeleton skeleton--lede" />
          </div>
        </div>
        <div className="surface">
          <div className="surface__hdr">
            {online ? (
              <span className="skeleton skeleton--heading" aria-hidden="true" />
            ) : (
              <span className="h-4">Waiting for a connection</span>
            )}
          </div>
          <div className="surface__body surface__body--reserve">
            {online ? (
              <span className="sr-only">Loading this page</span>
            ) : (
              <span className="muted fs-12">This page loads as soon as you are back online.</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
