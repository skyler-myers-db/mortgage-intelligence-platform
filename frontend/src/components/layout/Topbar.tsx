import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import { useHealth } from '../HealthProvider';
import { useFootprint } from '../FootprintProvider';

/**
 * Topbar — breadcrumbs, lender pill, environment pill, warehouse-status pill,
 * theme toggle, Genie toggle, Console toggle. Matches the prototype's BEM
 * (`topbar__crumbs`, `topbar__pill`, `topbar__icon-btn`).
 *
 * Topbar reads the shared `HealthProvider` snapshot (round-2 hole-finder
 * #21, 2026-04-23) instead of running its own `/api/health` poll. Cadence
 * (8s healthy / 3s degraded) lives in the provider so pills can't drift
 * from the DegradedBanner.
 */

const ROUTE_CRUMBS: Record<string, string> = {
  '/':                       'Home',
  '/portfolio-builder':      'Portfolio Builder',
  '/segment-intelligence':   'Segment Intelligence',
  '/lead-queue':             'Lead Queue',
  '/borrower-360':           'Borrower 360',
  '/offer-orchestrator':     'Offer Orchestrator',
  '/ask-genie':              'Ask Genie',
  '/admin-config':           'Admin',
};

function currentCrumb(path: string): string {
  if (path.startsWith('/borrower-360')) return 'Borrower 360';
  if (path.startsWith('/offer-orchestrator')) return 'Offer Orchestrator';
  return ROUTE_CRUMBS[path] ?? 'Home';
}

export function Topbar() {
  const { lender, theme, setTheme, genieOpen, setGenieOpen, consoleOpen, setConsoleOpen } = useApp();
  const { pathname } = useLocation();
  const crumb = currentCrumb(pathname);
  const { health } = useHealth();
  // R5-07 (2026-04-23): surface the footprint fallback as a muted chip
  // so operators aren't silently pinned to the 6-state default when
  // /api/config/footprint failed on cold-start. This is a separate
  // signal from /api/health (which drives DegradedBanner) — the
  // warehouse can be up while the footprint fetch is stale.
  const { usingFallback: footprintFallback } = useFootprint();
  // R6-11 (2026-04-23): on a deep-link refresh, three providers race
  // to report "cold start" simultaneously (DegradedBanner, footprint
  // fallback chip, route-level WarmingUpBlock). Suppress the footprint
  // chip for the first ~3s of mount so the first paint doesn't stack
  // two cold-start indicators before the degraded-banner takes over.
  // After the grace window, the chip still lights up if the fallback
  // is still in use.
  const [mountGraceOver, setMountGraceOver] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setMountGraceOver(true), 3000);
    return () => clearTimeout(t);
  }, []);

  const envLabel = (health?.app_env ?? 'loading').toLowerCase();
  const warehouseUp = health?.dependencies?.warehouse === 'up';
  const warehouseDot = !health
    ? 'dot amber'
    : warehouseUp
      ? 'dot is-heartbeat'
      : 'dot';
  const warehouseColor = !health
    ? undefined
    : warehouseUp
      ? undefined
      : { background: 'var(--signal-danger)' };

  return (
    <header className="topbar" role="banner">
      <div className="topbar__crumbs">
        <span>Mortgage Intelligence Platform</span>
        <span className="sep">/</span>
        <span className="cur">{crumb}</span>
      </div>
      <div className="topbar__spacer" />
      <div className="topbar__pill" title="Lender">
        <Icon name="building" size={12} />
        <span>{lender}</span>
      </div>
      <div
        className="topbar__pill"
        title={`Environment: ${envLabel}`}
      >
        <span className={`dot ${envLabel === 'production' ? 'green' : 'amber'}`} />
        <span>{envLabel}</span>
      </div>
      <div
        className="topbar__pill"
        title={
          health
            ? `Warehouse: ${warehouseUp ? 'up' : 'down'} · breaker ${
                health.circuit_breakers?.warehouse ?? 'unknown'
              }`
            : 'Warehouse: probing'
        }
      >
        <span className={warehouseDot} aria-hidden="true" style={warehouseColor} />
        <span style={{ fontFamily: 'var(--font-mono)' }}>
          {warehouseUp ? 'warehouse' : health ? 'offline' : '…'}
        </span>
      </div>
      {footprintFallback && mountGraceOver && (
        <span
          className="chip chip--warning"
          title="The /api/config/footprint fetch failed — showing the canonical 6-state fallback. The warehouse may still be cold-starting."
          data-testid="footprint-fallback-chip"
        >
          <Icon name="shield" size={10} />
          Footprint: fallback
        </span>
      )}
      <button
        className="topbar__icon-btn"
        onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        aria-label="Toggle theme"
        type="button"
      >
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={15} />
      </button>
      <button
        className={`topbar__icon-btn ${genieOpen ? 'is-active' : ''}`}
        onClick={() => setGenieOpen(!genieOpen)}
        title="Ask Genie"
        aria-label="Toggle Genie chat"
        aria-pressed={genieOpen}
        type="button"
      >
        <Icon name="sparkle" size={15} />
      </button>
      <button
        className={`topbar__icon-btn ${consoleOpen ? 'is-active' : ''}`}
        onClick={() => setConsoleOpen(!consoleOpen)}
        title="Console (theme, density, accent)"
        aria-label="Toggle console"
        aria-pressed={consoleOpen}
        type="button"
      >
        <Icon name="tweak" size={15} />
      </button>
    </header>
  );
}
