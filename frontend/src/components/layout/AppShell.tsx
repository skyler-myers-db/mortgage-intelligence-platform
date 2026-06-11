import { Suspense, useCallback, useEffect, useRef, type PropsWithChildren } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AppProvider, useApp } from '../AppContext';
import { HealthProvider, useHealth } from '../HealthProvider';
import { FootprintProvider } from '../FootprintProvider';
import { Rail } from './Rail';
import { Topbar } from './Topbar';
import { EvidenceDrawer } from '../mortgage/EvidenceDrawer';
import { DegradedBanner } from '../mortgage/DegradedBanner';
import { Icon } from '../Icon';
import { lazyWithPreload, preloadBestEffort } from '../../lib/lazyPreload';
import { createIdlePreloader } from '../../lib/prefetch';
import { clearActorScopedBrowserState } from '../../lib/actorScopedBrowserState';
import { clearActorScopedMemoryCaches } from '../../lib/actorScopedMemoryCaches';

const LazyConsole = lazyWithPreload(() =>
  import('./Console').then((module) => ({ default: module.Console })),
);

const LazyGenieChat = lazyWithPreload(() =>
  import('../mortgage/GenieChat').then((module) => ({ default: module.GenieChat })),
);

const preloadConsole = createIdlePreloader(() => LazyConsole.preload(), 5000);
const preloadGenieChat = createIdlePreloader(() => LazyGenieChat.preload(), 5000);
// NOTE (2026-06-10 perf audit): rolldown reports INEFFECTIVE_DYNAMIC_IMPORT
// here because drawerSources is also statically imported by several
// lazy-side components (EvidenceDrawer, GenieChat, LeadRowPreview, ...),
// so it lives in their SHARED lazy chunk rather than a chunk of its own.
// That is fine — the intent of this idle import() is to warm whichever
// chunk carries the evidence/source mapping before the first drawer
// open, and it does exactly that at runtime. Initial JS is unaffected
// (verified: index-*.js byte-stable with/without this line). Do not
// "fix" the warning by removing the preload or by forcing a separate
// chunk; both make first-drawer-open slower.
const preloadDrawerSources = createIdlePreloader(() => import('../../lib/drawerSources'), 5000);

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
 * HealthProvider wraps the whole shell so Topbar, AgentActivityLog, and
 * DegradedBanner share a single `/api/health` poll instead of each
 * running its own setInterval. Round-2 hole-finder #21, 2026-04-23.
 *
 * Accessibility:
 *   - First focusable: "Skip to main content" → `#main-content` (WCAG
 *     2.2 SC 2.4.1). Second: "Skip to workspace console" (unchanged).
 *     The skip targets both have `tabIndex={-1}` so the anchor jump
 *     actually moves focus instead of just scrolling.
 *   - Rail is `<nav aria-label="Primary navigation">`; Topbar is a
 *     `<header>` landmark (implicit `banner` role + explicit for AT
 *     parity). `<main id="main-content">` is the primary content
 *     landmark.
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

export function shouldResetActorScopedStateForActorChange(
  previousActorCacheKey: string | null,
  nextActorCacheKey: string | null,
): boolean {
  return previousActorCacheKey !== null && previousActorCacheKey !== nextActorCacheKey;
}

export function applyActorScopedStateTransition({
  previousActorCacheKey,
  nextActorCacheKey,
  clearQueryClient,
  clearAppState,
  clearBrowserState,
  clearMemoryState,
}: {
  previousActorCacheKey: string | null;
  nextActorCacheKey: string | null;
  clearQueryClient: () => void;
  clearAppState: () => void;
  clearBrowserState: () => void;
  clearMemoryState: () => void;
}): string | null {
  if (previousActorCacheKey === nextActorCacheKey) return previousActorCacheKey;
  if (shouldResetActorScopedStateForActorChange(previousActorCacheKey, nextActorCacheKey)) {
    clearQueryClient();
    clearAppState();
    clearBrowserState();
    clearMemoryState();
  }
  return nextActorCacheKey;
}

function AppShellInner({ children }: PropsWithChildren) {
  const { clearActorScopedState, consoleOpen, genieOpen, setGenieOpen } = useApp();
  const { health } = useHealth();
  const queryClient = useQueryClient();
  const actorCacheKeyRef = useRef<string | null>(null);

  useEffect(() => {
    const cancelConsole = preloadConsole();
    const cancelDrawer = preloadDrawerSources();
    const cancelGenie = preloadGenieChat();
    return () => {
      cancelConsole();
      cancelDrawer();
      cancelGenie();
    };
  }, []);

  useEffect(() => {
    const nextKey = health?.actor_cache_key ?? null;
    const previousKey = actorCacheKeyRef.current;
    actorCacheKeyRef.current = applyActorScopedStateTransition({
      previousActorCacheKey: previousKey,
      nextActorCacheKey: nextKey,
      clearQueryClient: () => queryClient.clear(),
      clearAppState: clearActorScopedState,
      clearBrowserState: clearActorScopedBrowserState,
      clearMemoryState: clearActorScopedMemoryCaches,
    });
  }, [clearActorScopedState, health?.actor_cache_key, queryClient]);

  const openGenie = useCallback(() => {
    setGenieOpen(true);
  }, [setGenieOpen]);

  const warmGenie = useCallback(() => {
    preloadBestEffort(LazyGenieChat.preload);
  }, []);

  return (
    <div className="app-shell">
      {/*
        Skip-links. Keyboard users tab once to get "Skip to main content",
        a second time for "Skip to workspace console". Visually hidden
        until focused, per the standard a11y pattern in
        frontend/src/design-system/components.css. Both targets carry
        `tabIndex={-1}` so the anchor-jump actually shifts focus rather
        than just scrolling the viewport.
      */}
      <a href="#main-content" className="sr-skip-link">
        Skip to main content
      </a>
      <a href="#workspace-console" className="sr-skip-link">
        Skip to workspace console
      </a>
      <Rail />
      <Topbar />
      <main id="main-content" tabIndex={-1} className="main">
        <DegradedBanner />
        {children}
      </main>
      <EvidenceDrawer />
      <Suspense
        fallback={(
          <aside
            id="workspace-console"
            className={`tweaks ${consoleOpen ? 'is-open' : ''}`}
            role="complementary"
            aria-label="Workspace console"
            aria-hidden={!consoleOpen}
          />
        )}
      >
        {consoleOpen ? (
          <LazyConsole />
        ) : (
          <aside
            id="workspace-console"
            className="tweaks"
            role="complementary"
            aria-label="Workspace console"
            aria-hidden="true"
          />
        )}
      </Suspense>
      {!genieOpen && (
        <button
          className="genie__fab"
          onClick={openGenie}
          onMouseEnter={warmGenie}
          onFocus={warmGenie}
          aria-label="Open Genie"
          type="button"
        >
          <Icon name="sparkle" size={22} />
        </button>
      )}
      <Suspense fallback={null}>
        {genieOpen ? <LazyGenieChat /> : null}
      </Suspense>
    </div>
  );
}
