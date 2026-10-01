import { Suspense, useCallback, useEffect, useLayoutEffect, useRef, type PropsWithChildren } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AppProvider, useApp } from '../AppContext';
import { HealthProvider, useHealth } from '../HealthProvider';
import { FootprintProvider } from '../FootprintProvider';
import { Rail } from './Rail';
import { Topbar } from './Topbar';
import { CommandPalette } from '../command/CommandPalette';
import { EvidenceDrawer } from '../mortgage/EvidenceDrawer';
import { DegradedBanner } from '../mortgage/DegradedBanner';
import { VersionNotice } from '../mortgage/VersionNotice';
import { GenieDock } from './GenieDock';
import { ConsoleBoundary, DrawerBoundary } from './ShellPanelBoundaries';
import { ShellToaster } from '../feedback/ShellToaster';
import { UnsavedChangesGuard } from '../feedback/UnsavedChangesGuard';
import { lazyWithPreload, preloadBestEffort } from '../../lib/lazyPreload';
import { createIdlePreloader } from '../../lib/prefetch';
import { observeActor, subscribeActorScope } from '../../lib/actorScope';
import { clearActorScopedMemoryCaches } from '../../lib/actorScopedMemoryCaches';
import { useExitRetained } from '../../hooks/useExitRetained';
import { useMainScroll } from '../../hooks/useMainScroll';
import { useRouteAnnouncer } from '../../hooks/useRouteAnnouncer';

const LazyConsole = lazyWithPreload(() =>
  import('./Console').then((module) => ({ default: module.Console })),
);

const LazyGenieChat = lazyWithPreload(() =>
  import('../mortgage/GenieChat').then((module) => ({ default: module.GenieChat })),
);

const preloadConsole = createIdlePreloader(() => LazyConsole.preload(), 5000);
const preloadGenieChat = createIdlePreloader(() => LazyGenieChat.preload(), 5000);

/**
 * AppShell — rail + topbar + main grid.
 *
 * DataMesh (a 36-circle CSS-animated SVG layer behind every page) was
 * removed 2026-04-23: combined with the body::before/::after ambient
 * radial-halo animations it was causing mouse-lag on ultra-wide and
 * high-DPI displays. Enterprise workspace UIs don't need ambient motion.
 *
 * The DegradedBanner renders nothing while /api/health is ok (no layout
 * shift in the happy path) and auto-retries while any dependency is down.
 *
 * HealthProvider wraps the whole shell so Topbar and DegradedBanner share a
 * single `/api/health` poll instead of running independent timers.
 *
 * Accessibility:
 *   - First focusable: "Skip to main content" → `#main-content` (WCAG
 *     2.2 SC 2.4.1). The workspace-console shortcut is emitted only while
 *     that complementary landmark is visible. Both visible skip targets use
 *     `tabIndex={-1}` so the anchor jump moves focus instead of only scrolling.
 *   - Rail is `<nav aria-label="Primary navigation">`; Topbar is a
 *     `<header>` landmark (implicit `banner` role + explicit for AT
 *     parity). `<main id="main-content">` is the primary content
 *     landmark.
 *   - Route continuity (audit 2026-09-21): `useMainScroll` resets / restores
 *     the persistent `.main` scroller per history entry, and
 *     `useRouteAnnouncer` sets the per-route document title, moves focus to
 *     the page heading and writes the page name into the one polite
 *     `.sr-only` live region below (WCAG 2.4.2, 2.4.3, 4.1.3).
 */
export function AppShell({ children }: PropsWithChildren) {
  return (
    <AppProvider>
      <HealthProvider>
        <FootprintProvider>
          <AppShellInner>{children}</AppShellInner>
        </FootprintProvider>
      </HealthProvider>
    </AppProvider>
  );
}

/**
 * Returns true when `/api/health` reports the stack as degraded. Thin
 * wrapper over the provider context so components that only need the
 * boolean don't have to destructure the full health payload.
 */
export function useIsHealthDegraded(): boolean {
  return useHealth().degraded;
}

function AppShellInner({ children }: PropsWithChildren) {
  const { clearActorScopedState, consoleOpen, genieOpen, setGenieOpen } = useApp();
  const { actorIdentity } = useHealth();
  const queryClient = useQueryClient();
  const mainRef = useRef<HTMLElement | null>(null);
  const routeAnnouncerRef = useRef<HTMLDivElement | null>(null);
  useMainScroll(mainRef);
  useRouteAnnouncer(mainRef, routeAnnouncerRef);
  // motion-01: keep the ONE Console <aside> mounted while it plays its exit
  // (09-console-and-layout.css), then swap in the empty placeholder. Swapping
  // on the closing commit replaced the node and cut the exit to 0ms. The lazy
  // Console owns the element, so a layout effect (it runs before the passive
  // effect that times the exit) points the ref at the landmark each commit.
  const consoleElementRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    consoleElementRef.current = document.getElementById('workspace-console');
  });
  const consoleMounted = useExitRetained(consoleOpen ? true : null, consoleElementRef) === true;

  useEffect(() => {
    const cancelConsole = preloadConsole();
    const cancelGenie = preloadGenieChat();
    return () => {
      cancelConsole();
      cancelGenie();
    };
  }, []);

  // The in-memory half of the actor boundary (lib/actorScope owns storage).
  // Declared before the observer below so it is subscribed first. On
  // 'cleared' (another actor's data was removed) and, as the W5a nobody
  // bridge, on 'closed' (a trusted nobody after a real owner: a 401, 403 or
  // anonymous body), drop the query cache, the shell's actor state (last
  // borrower, approvals, drawer, workspace) and the route-local caches. While
  // closed the stores' storage survives and their reads return null, so a
  // same-actor reopen shows it again.
  useEffect(
    () =>
      subscribeActorScope(({ reason }) => {
        if (reason !== 'cleared' && reason !== 'closed') return;
        queryClient.clear();
        clearActorScopedState();
        clearActorScopedMemoryCaches();
      }),
    [clearActorScopedState, queryClient],
  );

  // Every trusted actor observation goes to the gate. HealthProvider's
  // `actorIdentity` is set only by a TRUSTED probe (lib/healthTrust) or, before
  // the first one, by the primed session body: an unreachable, 5xx, offline
  // or unreadable probe, or a thrown one, is skipped, so a network blip or a
  // post-deploy 502 never reads as "the actor changed to nobody". Until the
  // first trusted observation the gate stays pending and private storage
  // reads nothing.
  useEffect(() => {
    if (actorIdentity !== null) observeActor({ key: actorIdentity.key });
  }, [actorIdentity]);

  const openGenie = useCallback(() => {
    setGenieOpen(true);
  }, [setGenieOpen]);

  const closeGenie = useCallback(() => {
    setGenieOpen(false);
  }, [setGenieOpen]);

  const warmGenie = useCallback(() => {
    preloadBestEffort(LazyGenieChat.preload);
  }, []);

  return (
    <div className="app-shell">
      {/*
        Skip-links. Keyboard users first get "Skip to main content". The
        workspace-console shortcut exists only while its destination is open.
        Links are visually hidden until focused, per the standard pattern in
        frontend/src/design-system/components.css; targets use tabIndex=-1 so
        the anchor jump shifts focus rather than only scrolling.
      */}
      <a href="#main-content" className="sr-skip-link">
        Skip to main content
      </a>
      {consoleOpen && (
        <a href="#workspace-console" className="sr-skip-link">
          Skip to workspace console
        </a>
      )}
      <Rail />
      <Topbar />
      <CommandPalette />
      {/* Persistent route announcer. Left empty on purpose: useRouteAnnouncer
          writes the page name here after each navigation, and React never
          re-renders its content away. */}
      <div
        ref={routeAnnouncerRef}
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-route-announcer=""
      />
      <main ref={mainRef} id="main-content" tabIndex={-1} className="main">
        <DegradedBanner />
        <VersionNotice />
        {children}
      </main>
      {/* Panel boundaries (audit states-01): a throw in the drawer, the
          Console or the Genie chat stays inside that panel's frame instead of
          reaching the root; see ShellPanelBoundaries. */}
      <DrawerBoundary>
        <EvidenceDrawer />
      </DrawerBoundary>
      <ConsoleBoundary>
        <Suspense
          fallback={(
            <aside
              id="workspace-console"
              className={`tweaks ${consoleOpen ? 'is-open' : ''}`}
              role="complementary"
              aria-label="Workspace console"
              aria-hidden={!consoleOpen}
              tabIndex={consoleOpen ? -1 : undefined}
            />
          )}
        >
          {consoleMounted ? (
            <LazyConsole />
          ) : (
            <aside
              id="workspace-console"
              className="tweaks"
              role="complementary"
              aria-label="Workspace console"
              aria-hidden="true"
              tabIndex={-1}
            />
          )}
        </Suspense>
      </ConsoleBoundary>
      {/* Mounted on first open and never unmounted: closing only hides the
          panel, so an in-flight Genie turn survives (see GenieDock). */}
      <GenieDock
        open={genieOpen}
        onOpen={openGenie}
        onClose={closeGenie}
        onWarm={warmGenie}
        Chat={LazyGenieChat}
      />
      {/* Shell feedback (audit states-05 / states-07): the toast region and
          the "Leave without saving?" guard for pages with unsaved work. */}
      <ShellToaster />
      <UnsavedChangesGuard />
    </div>
  );
}
