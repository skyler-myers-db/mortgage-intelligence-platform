import { useCallback, useContext, useEffect } from 'react';
import {
  UNSAFE_DataRouterContext,
  UNSAFE_DataRouterStateContext,
  useBlocker,
  type BlockerFunction,
} from 'react-router';
import { unsavedWorkMessage, useUnsavedWorkMessage } from '../../hooks/useUnsavedGuard';
import { UnsavedChangesDialog } from './UnsavedChangesDialog';

/**
 * The shell half of the unsaved-changes guard (2026-09-21 audit states-05).
 * Pages declare unsaved work with `useUnsavedGuard(dirty, message)`; this
 * component, mounted once in AppShell, turns it into a navigation block.
 *
 * Router detection: `useBlocker` only exists under a data router (main.tsx's
 * `createBrowserRouter`) and throws under a declarative one. Whether a data
 * router is present is read from React Router's own context, which is null
 * under `<MemoryRouter>` / `<BrowserRouter>`: a plain context read, so the
 * hook-calling child is simply not rendered there and the guard degrades to
 * the page's `beforeunload` alone. It is exported as `UNSAFE_` because it is
 * an integration point rather than an app API, and only its presence is read.
 *
 * The blocker is mounted only while some page is dirty, so a clean app pays
 * nothing on navigation and React Router's one-blocker-per-router rule holds
 * however many pages are dirty. It also stays mounted while it HOLDS a
 * navigation (the router's own blocker state, read from its state context
 * like the router above): a page that turns clean while "Leave without
 * saving?" is open (a save finishing behind the dialog) used to unmount the
 * blocker mid-block, which silently dropped the navigation. Now nothing is
 * at stake any more, so the held navigation proceeds (wave-1c follow-up #7).
 */
export function UnsavedChangesGuard() {
  const message = useUnsavedWorkMessage();
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  const routerState = useContext(UNSAFE_DataRouterStateContext);
  const holding = [...(routerState?.blockers.values() ?? [])].some((blocker) => blocker.state === 'blocked');
  if (!dataRouter || (message === null && !holding)) return null;
  return <NavigationBlocker message={message} />;
}

/**
 * Block only while some page is STILL dirty, and only for a new pathname.
 *
 * The live store read is what lets a redirect through after Leave. The
 * blocker stays registered until this guard re-renders from the store
 * publish, but the dirty page's unregister cleanup and the destination's
 * `<Navigate replace/>` mount effect run in the same passive-effect flush
 * (Cmd-K's bare /offer-orchestrator, the legacy /outreach-composer paths).
 * A predicate that ignored the store blocked that redirect with a blocker
 * about to be deleted, and the navigation was dropped: a blank page.
 *
 * A search or hash change stays on the page (Portfolio Builder's Run build
 * writes its filters to the query string, and the skip links are hash
 * targets), so it never asks. Known gap, accepted so Run build never
 * prompts: a same-path navigation the page did not start (Back to an
 * earlier build's query, a Genie handoff to /portfolio-builder?...) is not
 * guarded either, and Portfolio Builder's URL reconcile then replaces unrun
 * filters. The typed campaign setup is not in the URL and survives it.
 */
export const leavesThePage: BlockerFunction = ({ currentLocation, nextLocation }) =>
  unsavedWorkMessage() !== null && currentLocation.pathname !== nextLocation.pathname;

function NavigationBlocker({ message }: { message: string | null }) {
  const blocker = useBlocker(leavesThePage);
  const blocked = blocker.state === 'blocked';
  const stay = useCallback(() => blocker.reset?.(), [blocker]);
  const leave = useCallback(() => blocker.proceed?.(), [blocker]);
  // The page turned clean while the navigation was held: let it through.
  useEffect(() => {
    if (blocked && message === null) blocker.proceed?.();
  }, [blocked, blocker, message]);
  if (!blocked || message === null) return null;
  return <UnsavedChangesDialog message={message} onStay={stay} onLeave={leave} />;
}
