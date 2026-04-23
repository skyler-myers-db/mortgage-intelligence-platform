import type { PropsWithChildren } from 'react';
import { AppProvider } from '../AppContext';
import { HealthProvider } from '../HealthProvider';
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
 */
export function AppShell({ children }: PropsWithChildren) {
  return (
    <AppProvider>
      <HealthProvider>
        <FootprintProvider>
          <div className="app-shell">
            {/*
              Keyboard-only skip-link. Tabbing into the page from the browser
              chrome would otherwise land in the Rail and require ~30 Tab
              presses to reach the right-rail Console. Visually hidden until
              focused, per the standard a11y pattern in
              frontend/src/design-system/components.css.
            */}
            <a href="#workspace-console" className="sr-skip-link">
              Skip to workspace console
            </a>
            <Rail />
            <Topbar />
            <main className="main">
              <DegradedBanner />
              {children}
            </main>
            <EvidenceDrawer />
            <Console />
            <GenieChat />
          </div>
        </FootprintProvider>
      </HealthProvider>
    </AppProvider>
  );
}
