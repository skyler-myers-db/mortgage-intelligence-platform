import { lazy, Suspense, type ComponentType } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router';
import { Icon } from '../Icon';
import { useOptionalHealth } from '../HealthProvider';
import { EntradaMark } from '../brand/Entrada';
import {
  followUpsSummaryEnabled,
  followUpsSummaryQueryOptions,
  type FollowupSummary,
} from '../../lib/apiClients/followUpsSummary';
import { usePresenterMode } from '../../lib/presenterMode';
import { ROUTES } from '../../lib/routeMeta';
import { sessionQueryOptions, useAuditLedgerAccess } from '../../lib/sessionQuery';
import { Tooltip } from '../ui/Tooltip';
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
/** The settings link's name: its title used to be its only name (critic-08). */
const ADMIN_LABEL = 'Admin / settings';

const NoRoadmap: ComponentType = () => null;
// Two steps, as in lenderMarkLoader: when the stale-chunk listener cancels
// vite:preloadError, Vite's preload helper resolves the import to undefined,
// skipping the first .then (which it takes in with the import), so the
// undefined guard sits in the second one. Either way the rail renders M0
// alone, never a shell error.
const RailRoadmap = lazy<ComponentType>(() =>
  import('./RailRoadmap')
    .then((module) => module.default)
    .then(
      (roadmap: ComponentType | undefined) => ({ default: roadmap ?? NoRoadmap }),
      () => ({ default: NoRoadmap }),
    ),
);

type FollowUpsItemProps = { summary: FollowupSummary };
const NoFollowUps: ComponentType<FollowUpsItemProps> = () => null;
// The Follow-ups item is its own lazy chunk (D-shell-deviations-a): only the
// summary poll below is in the initial closure. Same two-step guard as
// RailRoadmap: a chunk that cannot load renders nothing, never a shell error.
const FollowUpsRailItem = lazy<ComponentType<FollowUpsItemProps>>(() =>
  import('./FollowUpsRailItem')
    .then((module) => module.default)
    .then(
      (item: ComponentType<FollowUpsItemProps> | undefined) => ({ default: item ?? NoFollowUps }),
      () => ({ default: NoFollowUps }),
    ),
);

export function Rail() {
  const { pathname } = useLocation();
  const canAccessAdmin = useAdminNavigationAccess();
  const canReadLedger = useAuditLedgerAccess();
  // False while the session is pending or errored: a customer never sees a demo slot by accident.
  const presenterMode = usePresenterMode();
  const onLedger = pathname === ROUTES.auditLedger.pattern;
  // Follow-ups due: polled once a minute for a session that names an actor and
  // is not expired; errors are silent (the item does not render).
  const session = useQuery(sessionQueryOptions());
  const connection = useOptionalHealth()?.connection;
  const followUps = useQuery(followUpsSummaryQueryOptions(followUpsSummaryEnabled(session.data, connection)));
  return (
    <nav className="rail" aria-label="Primary navigation" data-rum-target="nav">
      {/* R1 (critic-08): 'Entrada home' names the brand link; its old title
          was dropped, not moved to a Tooltip, under the W5e 0 KiB initial-JS cap. */}
      <Link to="/" className="rail__brand" aria-label="Entrada home">
        <EntradaMark size={32} />
      </Link>
      {/* The accessible name stays 'M0'; the module description is the tooltip (critic-08). */}
      <Tooltip content={`Module ${M0.id}: ${M0.name} — ${M0.desc}`}>
        <Link to="/" className="rail__item is-active" aria-current="page">
          <Icon name="target" size={18} className="ico" />
          <span className="mod">M{M0.id}</span>
        </Link>
      </Tooltip>
      {presenterMode && (
        <Suspense fallback={null}>
          <RailRoadmap />
        </Suspense>
      )}
      <div className="rail__spacer" />
      {followUps.data?.eligible === true && (
        <Suspense fallback={null}>
          <FollowUpsRailItem summary={followUps.data} />
        </Suspense>
      )}
      {/* The audit ledger for administrators and auditors (D-audit-reads-c3):
          the prototype rail ends with Settings only
          (design_files/Module 0 Prototype.html:1201-1202;
          deviation:rail-audit-ledger). */}
      {canReadLedger && (
        <Tooltip content={ROUTES.auditLedger.name}>
          <Link
            to={ROUTES.auditLedger.pattern}
            className="rail__item"
            aria-label={ROUTES.auditLedger.name}
            aria-current={onLedger ? 'page' : undefined}
          >
            <Icon name="audit" size={16} />
          </Link>
        </Tooltip>
      )}
      {canAccessAdmin && (
        <Tooltip content={ADMIN_LABEL}>
          <Link to="/admin-config" className="rail__item" aria-label={ADMIN_LABEL}>
            <Icon name="settings" size={16} />
          </Link>
        </Tooltip>
      )}
    </nav>
  );
}
