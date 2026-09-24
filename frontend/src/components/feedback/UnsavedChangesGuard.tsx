import { useCallback, useContext } from 'react';
import { UNSAFE_DataRouterContext, useBlocker, type BlockerFunction } from 'react-router';
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
 * however many pages are dirty.
 */
export function UnsavedChangesGuard() {
  const message = useUnsavedWorkMessage();
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  if (!dataRouter || message === null) return null;
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

function NavigationBlocker({ message }: { message: string }) {
  const blocker = useBlocker(leavesThePage);
  const stay = useCallback(() => blocker.reset?.(), [blocker]);
  const leave = useCallback(() => blocker.proceed?.(), [blocker]);
  if (blocker.state !== 'blocked') return null;
  return <UnsavedChangesDialog message={message} onStay={stay} onLeave={leave} />;
}
