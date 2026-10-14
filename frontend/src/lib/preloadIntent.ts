import { saveDataRequested } from './prefetch';
import { preloadRouteForPath } from './routePreloaders';

/**
 * Warm a link's route CHUNK on intent (2026-09-21 audit bundle-09 item 1):
 * spread the result on a Link, and a pointer entering it or keyboard focus
 * landing on it starts the route module's download, so the click does not
 * wait on a cold chunk.
 *
 * Chunks only. Never a query, a prefetchQuery or a route-data prefetch:
 * opening a dossier or an offer writes audit rows (VIEW_BORROWER,
 * RECOMMEND_OFFER, DRAFT_OUTREACH), so those reads stay click-only. Nothing
 * is preloaded while the browser asks to save data.
 *
 * Its own module, not lib/routePreloaders: that one ships in the initial
 * chunk (RouteNav), and this helper rides the lazy chunks that use it (the
 * Console), so the first paint carries none of it.
 */
export interface PreloadIntentProps {
  onPointerEnter: () => void;
  onFocus: () => void;
}

export function preloadIntentProps(path: string): PreloadIntentProps {
  const warm = () => {
    if (!saveDataRequested()) preloadRouteForPath(path);
  };
  return { onPointerEnter: warm, onFocus: warm };
}
