// Shell-initial and its frames render only on failure: React Compiler memo
// caches would roughly double these small renderers in the initial chunk for
// no measurable render saving (same call as DegradedBanner.tsx and
// RouteFallback.tsx).
'use no memo';

import { useCallback, useRef, type ReactNode } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useModalDialog } from '../../hooks/useModalDialog';
import { queryKeys } from '../../lib/queryKeys';
import { useApp } from '../AppContext';
import { ErrorBoundary } from '../ErrorBoundary';
import { Icon } from '../Icon';
import { useGeniePanelDismissal } from '../mortgage/useGeniePanelDismissal';

/**
 * Error boundaries for the shell's three panels (2026-09-21 UI/UX audit
 * states-01, shell-01): the Console right rail, the evidence drawer and the
 * floating Genie chat. Without them a throw in any panel reached the root
 * boundary and replaced the whole workspace, route included.
 *
 * Each boundary renders the recovery surface (`ErrorSurface`, variant
 * `panel`) INSIDE a healthy copy of the panel's own frame, built from the
 * prototype BEM the panel already uses (`.tweaks`, `.drawer`, `.genie`,
 * `.drawer__close`): the panel keeps its place, header and Close, and the
 * route, the rail, the topbar and the other panels stay mounted. Close only
 * hides the panel; it never clears the error. Try again is always the user's
 * click and only re-renders the panel's children.
 *
 * A panel that threw on a malformed payload would re-throw on the same cached
 * query when it re-mounts (the evidence drawer reads the lineage manifest
 * with `staleTime: Infinity`; any cached query hands its data to the first
 * render before a refetch starts). So the Console's and the drawer's Try
 * again, and the drawer frame's Close, first drop the panel's OWN cached
 * queries that no mounted component observes: the crashed panel is
 * unmounted, so its queries have no observer. The reset is scoped to the
 * panel's key prefixes (`DRAWER_RESET_SCOPES`, `CONSOLE_RESET_SCOPES`); the
 * route boundary's app-wide reset (ErrorBoundaryRoute) used to drop every
 * unobserved query in the app, other routes' warm caches included (wave-3
 * error-telemetry review). Removing a query never fetches it; the panel
 * reads again only when the user's Try again or next open re-mounts it. The
 * Genie chat reads no query (its transcript lives in
 * lib/genieConversationStore), so its Try again only re-mounts it.
 *
 * This module is in the initial chunk on purpose: it must render when the
 * panel's lazy chunk (Console, GenieChat) is the thing that failed.
 */

interface BoundaryProps {
  children: ReactNode;
}

/** The evidence drawer's reads: the lineage manifest and the admin asset metadata. */
export const DRAWER_RESET_SCOPES: readonly QueryKey[] = [queryKeys.lineageManifest(), ['mip', 'asset']];
/** The Console's read: the actor's recent activity. */
export const CONSOLE_RESET_SCOPES: readonly QueryKey[] = [['audit', 'my-events']];

/**
 * Drop the cached queries under `scopes` that no mounted component observes.
 * Zero observers, not `type: 'inactive'`: a query held only by a disabled
 * observer is inactive yet still mounted. Never fetches.
 */
function useScopedQueryReset(scopes: readonly QueryKey[]): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    for (const queryKey of scopes) {
      queryClient.removeQueries({ queryKey, predicate: (query) => query.getObserversCount() === 0 });
    }
  }, [queryClient, scopes]);
}

/** The Console rail. Keeps `#workspace-console` so the skip link and the exit animation still find it. */
export function ConsoleBoundary({ children }: BoundaryProps) {
  const resetConsoleQueries = useScopedQueryReset(CONSOLE_RESET_SCOPES);
  return (
    <ErrorBoundary
      boundary="console"
      variant="panel"
      routeLabel="The Console"
      onRetry={resetConsoleQueries}
      frame={(surface) => <ConsoleFrame>{surface}</ConsoleFrame>}
    >
      {children}
    </ErrorBoundary>
  );
}

function ConsoleFrame({ children }: BoundaryProps) {
  const { consoleOpen, setConsoleOpen } = useApp();
  return (
    <aside
      id="workspace-console"
      className={`tweaks ${consoleOpen ? 'is-open' : ''}`}
      role="complementary"
      aria-label="Workspace console"
      aria-hidden={!consoleOpen}
      inert={!consoleOpen}
      tabIndex={-1}
    >
      <div className="tweaks__hdr">
        <Icon name="tweak" size={14} className="tweaks__hdr-icon" />
        <div className="tweaks__title">Console</div>
        <button
          className="drawer__close"
          onClick={() => setConsoleOpen(false)}
          aria-label="Close console"
          type="button"
        >
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="tweaks__body">{children}</div>
    </aside>
  );
}

/**
 * The evidence drawer. Closing it resets the boundary (resetKey) after the
 * frame's Close has dropped the drawer's unobserved cached queries, so the
 * next open renders the drawer afresh and re-reads its payload instead of
 * re-throwing.
 */
export function DrawerBoundary({ children }: BoundaryProps) {
  const { drawer } = useApp();
  const resetDrawerQueries = useScopedQueryReset(DRAWER_RESET_SCOPES);
  return (
    <ErrorBoundary
      boundary="drawer"
      variant="panel"
      routeLabel="The evidence drawer"
      onRetry={resetDrawerQueries}
      resetKey={drawer ? 'open' : 'closed'}
      frame={(surface) => <DrawerFrame>{surface}</DrawerFrame>}
    >
      {children}
    </ErrorBoundary>
  );
}

function DrawerFrame({ children }: BoundaryProps) {
  const { drawer, setDrawer } = useApp();
  const open = drawer !== null;
  const drawerRef = useRef<HTMLDialogElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const resetDrawerQueries = useScopedQueryReset(DRAWER_RESET_SCOPES);
  // This frame renders only in the error state, so every close (Close,
  // Escape, the backdrop) drops the crashed drawer's cached payload before
  // the resetKey re-mounts it closed; its next open reads again.
  const close = useCallback(() => {
    resetDrawerQueries();
    setDrawer(null);
  }, [resetDrawerQueries, setDrawer]);
  // Modal like the drawer it stands in for (the same native <dialog>): focus
  // starts on Close, Escape and a backdrop press close, and focus returns to
  // the chip that opened it.
  useModalDialog({ open, dialogRef: drawerRef, initialFocusRef: closeRef, onDismiss: close, backdrop: 'outside' });
  return (
      <dialog
        ref={drawerRef}
        className={`drawer ${open ? 'is-open' : ''}`}
        aria-label="Data source and lineage"
        aria-hidden={!open || undefined}
        inert={!open}
      >
        <div className="drawer__hdr">
          <div className="drawer__source-icon">
            <Icon name="db" size={16} />
          </div>
          <div className="drawer__hdr-main">
            <div className="drawer__title">Data source</div>
          </div>
          <button ref={closeRef} className="drawer__close" onClick={close} aria-label="Close drawer" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="drawer__body">{children}</div>
      </dialog>
  );
}

interface GenieBoundaryProps extends BoundaryProps {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}

/**
 * The floating Genie chat. No resetKey: closing and reopening shows the same
 * surface, and only Try again re-mounts the chat. A re-mount resumes from
 * the conversation store; it never re-submits a question.
 */
export function GenieBoundary({ open, onOpen, onClose, children }: GenieBoundaryProps) {
  return (
    <ErrorBoundary
      boundary="genie"
      variant="panel"
      routeLabel="Genie"
      frame={(surface) => (
        <GenieFrame open={open} onOpen={onOpen} onClose={onClose}>
          {surface}
        </GenieFrame>
      )}
    >
      {children}
    </ErrorBoundary>
  );
}

function GenieFrame({ open, onOpen, onClose, children }: GenieBoundaryProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const fabRef = useRef<HTMLButtonElement | null>(null);
  // Non-modal like the chat: no trap; Escape closes it only while focus is
  // inside, and focus returns to the launcher that opened it.
  useGeniePanelDismissal({ open, panelRef, inputRef: closeRef, fabRef, onClose });
  return (
    <>
      <button
        ref={fabRef}
        className={`genie__fab ${open ? 'is-hidden' : ''}`}
        onClick={onOpen}
        aria-label="Open Genie"
        type="button"
      >
        <Icon name="sparkle" size={22} />
      </button>
      <div
        ref={panelRef}
        className={`genie ${open ? 'is-open' : ''}`}
        tabIndex={-1}
        role="dialog"
        aria-label="Genie chat"
        aria-keyshortcuts="Escape"
        aria-hidden={!open}
      >
        <div className="genie__hdr">
          <div className="genie__avatar" />
          <div className="genie__title">Ask Genie</div>
          <button ref={closeRef} className="drawer__close" onClick={onClose} aria-label="Close Genie" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="genie__body">{children}</div>
      </div>
    </>
  );
}
