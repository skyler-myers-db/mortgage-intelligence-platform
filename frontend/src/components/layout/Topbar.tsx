import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import { useHealth } from '../HealthProvider';
import { useFootprint } from '../FootprintProvider';
import { api, type HealthPayload } from '../../lib/api';
import type { LeadSummary } from '../../types';

/**
 * Single status pill that consolidates env + dep state for the topbar.
 *
 * Replaces the prior 3-pill row (sandbox / warehouse·live / offline)
 * which was always-visible noise on every page when the system was
 * healthy. 2026-05-04 user feedback: "wouldn't every page say
 * Warehouse · live? how is that useful?" — the answer is "only useful
 * when something's wrong." The compact pill renders just a status dot
 * + a single word (Live / Degraded / Probing); the env name and the
 * per-dependency breakdown surface in the title tooltip on hover, so
 * the operator can drill in without burning persistent screen real
 * estate.
 */
export function systemStatusViewModel(health: HealthPayload | null): {
  dotClass: string;
  label: 'Probing' | 'Live' | 'Degraded';
  tooltip: string;
  ariaLabel: string;
} {
  // Health states:
  //   probing  → no payload yet; gray dot, tooltip "first probe in flight"
  //   live     → status==="ok" + every tracked dep === "up"
  //   degraded → status==="degraded" OR any dep down OR any breaker open
  const isProbing = !health;
  const deps = health?.dependencies ?? {};
  const breakers = health?.circuit_breakers ?? {};
  const depEntries = [
    ['warehouse', deps.warehouse],
    ['lakebase', deps.lakebase],
    ['genie', deps.genie],
  ] as const;
  const hasDependencyDetails = depEntries.some(([, state]) => state !== undefined);
  const allUp =
    deps.warehouse === 'up' && deps.lakebase === 'up' && deps.genie === 'up';
  const anyBreakerOpen = Object.values(breakers).some(
    (s) => s === 'open' || s === 'half_open',
  );
  const live =
    !isProbing &&
    health?.status !== 'degraded' &&
    !anyBreakerOpen &&
    (hasDependencyDetails ? allUp : health?.status === 'ok');

  const dotClass = isProbing
    ? 'dot amber'
    : live
      ? 'dot is-heartbeat'
      : 'dot danger';
  const label = isProbing ? 'Probing' : live ? 'Live' : 'Degraded';

  const dependencySummary = hasDependencyDetails
    ? depEntries.map(([name, state]) => `${name}=${state ?? 'not reported'}`).join(' · ')
    : 'dependency details not reported';
  const breakerSummary = Object.entries(breakers)
    .map(([k, v]) => `${k}=${v}`)
    .join(' / ');
  const tooltip = isProbing
    ? 'System status · probing — first /api/health probe still in flight.'
    : `System status · ${live ? 'live' : 'degraded'}\n` +
      dependencySummary +
      (breakerSummary ? `\nbreakers ${breakerSummary}` : '');

  return {
    dotClass,
    label,
    tooltip,
    ariaLabel: `System status: ${label}.`,
  };
}

function SystemStatusPill({ health }: { health: HealthPayload | null }) {
  const status = systemStatusViewModel(health);

  return (
    <div
      className="topbar__pill"
      title={status.tooltip}
      aria-label={status.ariaLabel}
      data-testid="system-status-pill"
    >
      <span className={status.dotClass} aria-hidden="true" />
      <span className="topbar__pill-label">
        {status.label}
      </span>
    </div>
  );
}

/**
 * Topbar — breadcrumbs, tenant pill, environment pill, warehouse-status pill,
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
  const navigate = useNavigate();
  const crumb = currentCrumb(pathname);
  const { health } = useHealth();
  // Surface the footprint fallback as a muted chip so operators are not
  // silently pinned to generic geography metadata when /api/config/footprint
  // failed on cold-start. This is separate from /api/health (which drives
  // DegradedBanner) because the warehouse can be up while the footprint fetch
  // is stale.
  const { usingFallback: footprintFallback } = useFootprint();
  // R6-11 (2026-04-23): on a deep-link refresh, three providers race
  // to report "cold start" simultaneously (DegradedBanner, footprint
  // fallback chip, route-level WarmingUpBlock). Suppress the footprint
  // chip for the first ~3s of mount so the first paint doesn't stack
  // two cold-start indicators before the degraded-banner takes over.
  // After the grace window, the chip still lights up if the fallback
  // is still in use.
  const [mountGraceOver, setMountGraceOver] = useState(false);
  const [borrowerQuery, setBorrowerQuery] = useState('');
  const [borrowerResults, setBorrowerResults] = useState<LeadSummary[]>([]);
  const [borrowerSearchTerm, setBorrowerSearchTerm] = useState('');
  const [borrowerSearchStatus, setBorrowerSearchStatus] = useState<'idle' | 'loading' | 'empty' | 'error'>('idle');
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const t = setTimeout(() => setMountGraceOver(true), 3000);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      e.preventDefault();
      searchInputRef.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    const q = borrowerQuery.trim();
    if (q.length < 2) {
      setBorrowerResults([]);
      setBorrowerSearchTerm('');
      setBorrowerSearchStatus('idle');
      return;
    }
    const ctrl = new AbortController();
    setBorrowerSearchStatus('loading');
    const t = window.setTimeout(() => {
      api
        .borrowerSearch(q, ctrl.signal)
        .then((rows) => {
          if (ctrl.signal.aborted) return;
          setBorrowerResults(rows);
          setBorrowerSearchTerm(q);
          setBorrowerSearchStatus(rows.length > 0 ? 'idle' : 'empty');
          setSearchOpen(true);
        })
        .catch(() => {
          if (ctrl.signal.aborted) return;
          setBorrowerResults([]);
          setBorrowerSearchTerm(q);
          setBorrowerSearchStatus('error');
          setSearchOpen(true);
        });
    }, 180);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [borrowerQuery]);

  const openBorrower = (id: string) => {
    setSearchOpen(false);
    setBorrowerQuery('');
    setBorrowerResults([]);
    setBorrowerSearchTerm('');
    setBorrowerSearchStatus('idle');
    navigate(`/borrower-360/${id}`);
  };

  const submitBorrowerSearch = async () => {
    const q = borrowerQuery.trim();
    if (q.length < 2) {
      setSearchOpen(true);
      setBorrowerSearchStatus('empty');
      return;
    }
    const cached =
      borrowerSearchTerm === q
        ? borrowerResults.find((row) => row.borrower_id.toUpperCase() === q.toUpperCase()) ?? borrowerResults[0]
        : undefined;
    if (cached) {
      openBorrower(cached.borrower_id);
      return;
    }
    setSearchOpen(true);
    setBorrowerSearchStatus('loading');
    try {
      const rows = await api.borrowerSearch(q);
      setBorrowerResults(rows);
      setBorrowerSearchTerm(q);
      const exact = rows.find((row) => row.borrower_id.toUpperCase() === q.toUpperCase());
      const first = exact ?? rows[0];
      if (first) {
        openBorrower(first.borrower_id);
      } else {
        setBorrowerSearchStatus('empty');
      }
    } catch {
      setBorrowerResults([]);
      setBorrowerSearchTerm(q);
      setBorrowerSearchStatus('error');
    }
  };

  return (
    <header className="topbar" role="banner">
      <div className="topbar__crumbs">
        <span>Mortgage Intelligence Platform</span>
        <span className="sep">/</span>
        <span className="cur">{crumb}</span>
      </div>
      <form
        className="topbar__search"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void submitBorrowerSearch();
        }}
      >
        <Icon name="search" size={12} />
        <input
          ref={searchInputRef}
          value={borrowerQuery}
          onChange={(e) => setBorrowerQuery(e.target.value)}
          onFocus={() => setSearchOpen(true)}
          onBlur={() => window.setTimeout(() => setSearchOpen(false), 120)}
          placeholder="Search borrower, ZIP, city, county, state"
          aria-label="Search borrowers"
        />
        {searchOpen && borrowerQuery.trim().length >= 2 && (borrowerResults.length > 0 || borrowerSearchStatus !== 'idle') && (
          <div className="topbar__search-results" role="listbox">
            {borrowerResults.slice(0, 5).map((row) => (
              <button
                key={row.borrower_id}
                type="button"
                role="option"
                className="topbar__search-result"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => openBorrower(row.borrower_id)}
              >
                <span className="mono">{row.borrower_id}</span>
                <span>{row.city}, {row.state} · {row.zip}</span>
              </button>
            ))}
            {borrowerSearchStatus === 'loading' && (
              <div className="topbar__search-status" role="status">
                Searching borrower, ZIP, city, county, and state...
              </div>
            )}
            {borrowerSearchStatus === 'empty' && borrowerResults.length === 0 && (
              <div className="topbar__search-status" role="status">
                No borrower, ZIP, city, county, or state matches "{borrowerSearchTerm || borrowerQuery.trim()}".
              </div>
            )}
            {borrowerSearchStatus === 'error' && (
              <div className="topbar__search-status topbar__search-status--error" role="status">
                Search is temporarily unavailable.
              </div>
            )}
          </div>
        )}
      </form>
      <div className="topbar__actions">
      {/* Tenant pill — display-only label for the configured lender. The
          backend applies lender configuration; the UI does not support
          arbitrary client-side lender switching. */}
      <div
        className="topbar__pill"
        title={`Configured tenant · ${lender}. Lender configuration is applied server-side.`}
        aria-label={`Configured tenant: ${lender}`}
      >
        <Icon name="building" size={12} />
        <span>{lender}</span>
      </div>
      {/*
        Single "system status" pill consolidating environment + warehouse
        + lakebase + genie state. Replaces the prior 3-pill row (sandbox /
        warehouse · live / offline) which was always-visible noise on
        every page even when the system was healthy. 2026-05-04 user
        feedback: "wouldn't every page say Warehouse · live? how is that
        useful?" — the answer is: only useful when something's wrong. The
        new pill shows just a status dot + "Live" / "Degraded" /
        "Probing", with the env name + per-dep breakdown surfaced via
        the title tooltip on hover. The dot color carries the severity
        signal (green=live, amber=degraded, gray=probing).
      */}
      <SystemStatusPill health={health} />
      {footprintFallback && mountGraceOver && (
        <span
          className="chip chip--warning"
          title="The /api/config/footprint fetch failed — showing generic US-state metadata until the footprint endpoint recovers."
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
      </div>
    </header>
  );
}
