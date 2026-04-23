import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import type { HealthPayload } from '../mortgage/DegradedBanner';

/**
 * Topbar — breadcrumbs, lender pill, environment pill, warehouse-status pill,
 * theme toggle, Genie toggle, Console toggle. Matches the prototype's BEM
 * (`topbar__crumbs`, `topbar__pill`, `topbar__icon-btn`).
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

  // Live health state for the environment + warehouse pills. Pulled from
  // /api/health every 30s; the DegradedBanner uses the same endpoint but
  // polls more aggressively when degraded. No auth header required — the
  // Databricks App runtime injects the workspace identity.
  const [health, setHealth] = useState<HealthPayload | null>(null);
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetch('/api/health');
        if (res.ok) {
          const json = (await res.json()) as HealthPayload;
          if (!cancelled) setHealth(json);
        }
      } catch {
        // Swallow — DegradedBanner surfaces outright failures; the pills
        // just display last-known state.
      }
    };
    void tick();
    const id = window.setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
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
