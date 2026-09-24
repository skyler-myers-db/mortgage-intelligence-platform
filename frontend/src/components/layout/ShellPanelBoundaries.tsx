import { useCallback, useRef, type ReactNode } from 'react';
import { useFocusTrap } from '../../hooks/useFocusTrap';
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
 * This module is in the initial chunk on purpose: it must render when the
 * panel's lazy chunk (Console, GenieChat) is the thing that failed.
 */

interface BoundaryProps {
  children: ReactNode;
}

/** The Console rail. Keeps `#workspace-console` so the skip link and the exit animation still find it. */
export function ConsoleBoundary({ children }: BoundaryProps) {
  return (
    <ErrorBoundary
      boundary="console"
      variant="panel"
      routeLabel="The Console"
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
 * The evidence drawer. Closing it resets the boundary (resetKey), so the
 * next open renders the drawer afresh instead of the old surface.
 */
export function DrawerBoundary({ children }: BoundaryProps) {
  const { drawer } = useApp();
  return (
    <ErrorBoundary
      boundary="drawer"
      variant="panel"
      routeLabel="The evidence drawer"
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
  const drawerRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const close = useCallback(() => setDrawer(null), [setDrawer]);
  // Modal like the drawer it stands in for: focus starts on Close, Escape
  // closes, and focus returns to the chip that opened it.
  useFocusTrap({ open, containerRef: drawerRef, initialFocusRef: closeRef, onClose: close });
  return (
    <>
      <div className={`drawer-scrim ${open ? 'is-open' : ''}`} onClick={close} aria-hidden={!open} />
      <aside
        ref={drawerRef}
        className={`drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="Data source and lineage"
        aria-hidden={!open}
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
      </aside>
    </>
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
