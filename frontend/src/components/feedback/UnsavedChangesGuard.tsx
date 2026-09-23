import { useCallback, useContext } from 'react';
import { UNSAFE_DataRouterContext, useBlocker, type BlockerFunction } from 'react-router';
import { useUnsavedWorkMessage } from '../../hooks/useUnsavedGuard';
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
 * Leaving means a new pathname. A search or hash change stays on the page
 * (Portfolio Builder's Run build writes its filters to the query string, and
 * the skip links are hash targets), so it never asks.
 */
export const leavesThePage: BlockerFunction = ({ currentLocation, nextLocation }) =>
  currentLocation.pathname !== nextLocation.pathname;

function NavigationBlocker({ message }: { message: string }) {
  const blocker = useBlocker(leavesThePage);
  const stay = useCallback(() => blocker.reset?.(), [blocker]);
  const leave = useCallback(() => blocker.proceed?.(), [blocker]);
  if (blocker.state !== 'blocked') return null;
  return <UnsavedChangesDialog message={message} onStay={stay} onLeave={leave} />;
}
