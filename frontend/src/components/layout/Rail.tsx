import { lazy, Suspense } from 'react';
import { Link, useLocation } from 'react-router';
import { Icon } from '../Icon';
import { EntradaMark } from '../brand/Entrada';
import { usePresenterMode } from '../../lib/presenterMode';
import { ROUTES } from '../../lib/routeMeta';
import { useAuditLedgerAccess } from '../../lib/sessionQuery';
import { useAdminNavigationAccess } from './RouteNav';

/**
 * Left module rail. Vertical strip, 72px wide. M0 ships today and lights up
 * whenever the user is on any Module 0 route (all current routes are).
 *
 * M1-M4, the modules on the published roadmap, render only in presenter mode
 * (deviation:rail-roadmap-presenter-only; critic-05, shell-09;
 * D-shell-deviations-e2): a customer workspace shows only what it can open.
 * They live in RailRoadmap, a lazy chunk requested only while the flag is
 * on, as aria-disabled buttons with a Tooltip description; a chunk that
 * fails to load renders nothing (never a shell error).
 *
 * Copy note (2026-04-23 hole-finder round 2): avoid the word "live" on
 * the rail since Module 0 data refreshes nightly via Delta Share, not
 * in real time. "Ships today" keeps the roadmap cue without implying a
 * streaming-data posture.
 */

const M0 = { id: 0, name: 'Top-of-Funnel', desc: 'Lead generation + borrower segmentation (ships today).' } as const;

const RailRoadmap = lazy(() => import('./RailRoadmap').catch(() => ({ default: () => null })));

export function Rail() {
  const { pathname } = useLocation();
  const canAccessAdmin = useAdminNavigationAccess();
  const canReadLedger = useAuditLedgerAccess();
  // False while the session is pending or errored: a customer never sees a demo slot by accident.
  const presenterMode = usePresenterMode();
  const onLedger = pathname === ROUTES.auditLedger.pattern;
  return (
    <nav className="rail" aria-label="Primary navigation" data-rum-target="nav">
      <Link to="/" className="rail__brand" title="Entrada — Mortgage Intelligence Platform" aria-label="Entrada home">
        <EntradaMark size={32} />
      </Link>
      <Link
        to="/"
        className="rail__item is-active"
        title={`Module ${M0.id}: ${M0.name} — ${M0.desc}`}
        aria-current="page"
      >
        <Icon name="target" size={18} className="ico" />
        <span className="mod">M{M0.id}</span>
      </Link>
      {presenterMode && (
        <Suspense fallback={null}>
          <RailRoadmap />
        </Suspense>
      )}
      <div className="rail__spacer" />
      {/* The audit ledger for administrators and auditors (D-audit-reads-c3):
          the prototype rail ends with Settings only
          (design_files/Module 0 Prototype.html:1201-1202;
          deviation:rail-audit-ledger). */}
      {canReadLedger && (
        <Link
          to={ROUTES.auditLedger.pattern}
          className="rail__item"
          aria-label={ROUTES.auditLedger.name}
          title={ROUTES.auditLedger.name}
          aria-current={onLedger ? 'page' : undefined}
        >
          <Icon name="audit" size={16} />
        </Link>
      )}
      {canAccessAdmin && (
        <Link to="/admin-config" className="rail__item" title="Admin / settings">
          <Icon name="settings" size={16} />
        </Link>
      )}
    </nav>
  );
}
