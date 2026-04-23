import type { PropsWithChildren } from 'react';
import { AppProvider } from '../AppContext';
import { HealthProvider, useHealth } from '../HealthProvider';
import { FootprintProvider } from '../FootprintProvider';
import { Rail } from './Rail';
import { Topbar } from './Topbar';
import { Console } from './Console';
import { EvidenceDrawer } from '../mortgage/EvidenceDrawer';
import { GenieChat } from '../mortgage/GenieChat';
import { DegradedBanner } from '../mortgage/DegradedBanner';

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

function AppShellInner({ children }: PropsWithChildren) {
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
      <Console />
      <GenieChat />
    </div>
  );
}
