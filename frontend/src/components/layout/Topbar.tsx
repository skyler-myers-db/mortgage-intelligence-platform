import { useLocation } from 'react-router-dom';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';

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
  return ROUTE_CRUMBS[path] ?? 'Module 0';
}

export function Topbar() {
  const { lender, theme, setTheme, genieOpen, setGenieOpen, consoleOpen, setConsoleOpen } = useApp();
  const { pathname } = useLocation();
  const crumb = currentCrumb(pathname);

  return (
    <header className="topbar" role="banner">
      <div className="topbar__crumbs">
        <span className="mono" style={{ color: 'var(--text-3)', fontSize: 11, letterSpacing: '0.06em' }}>WORKSPACE /</span>
        <span>mip-demo-app</span>
        <span className="sep">/</span>
        <span>Module 0: Top of Funnel</span>
        <span className="sep">/</span>
        <span className="cur">{crumb}</span>
      </div>
      <div className="topbar__spacer" />
      <div className="topbar__pill" title="Demo lender">
        <Icon name="building" size={12} />
        <span>{lender}</span>
      </div>
      <div className="topbar__pill" title="Environment: sandbox">
        <span className="dot amber" />
        <span>demo.sandbox</span>
      </div>
      <div className="topbar__pill" title="Databricks warehouse: running">
        <span className="dot" />
        <span style={{ fontFamily: 'var(--font-mono)' }}>serverless-xl</span>
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
