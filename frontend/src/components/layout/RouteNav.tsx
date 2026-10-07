import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { NavLink } from 'react-router';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { useRouteNavDock } from '../../hooks/useRouteNavDock';
import { saveDataRequested } from '../../lib/prefetch';
import { prefetchRouteData } from '../../lib/routeDataPrefetch';
import { preloadRouteForPath } from '../../lib/routePreloaders';
import {
  NAVIGATION_GROUPS,
  ROUTES,
  borrowerPath,
  offerPath,
  type AppPath,
  type NavigationRouteId,
} from '../../lib/routeMeta';
import { sessionQueryOptions, useAuditLedgerAccess } from '../../lib/sessionQuery';

/**
 * Secondary route nav. APP-ADDED ELEMENT: the prototype is a single screen
 * with a rail plus `.segmented` (design_files/index.html:926-935) and has no
 * route nav; our app splits Module 0 across routes for the linear user flow
 * (portfolio → segments → leads → borrower → offer → …).
 * Two unlabelled clusters (deviation:route-nav-clusters; flow-07, shell-09):
 * the lead workflow, then the insight and reference tools flush right, each
 * a list named for assistive technology. No count badges (nav-count-badges).
 *
 * Underline links (`.route-nav__link` / `.route-nav__label`; 2026-09-21
 * audit visual-05, M part): Geist sans 13/500 with a 2px --accent-ink
 * indicator under the current page, built from the prototype's token
 * vocabulary. It used to be a strip of bordered Geist Mono `.filter` chips,
 * which made navigation read like one more filter row; `.filter` /
 * `.filter__value` stay reserved for real filter pills.
 */

/**
 * Where a nav link points. Labels and icons come from the route registry
 * (lib/routeMeta, audit shell-08); only the two detail destinations are
 * resolved here, to the last borrower the actor opened.
 */
function navTargetFor(id: NavigationRouteId, lastBorrowerId: string | null): AppPath {
  if (id === 'borrowerIndex' && lastBorrowerId) return borrowerPath(lastBorrowerId);
  if (id === 'offerIndex' && lastBorrowerId) return offerPath(lastBorrowerId);
  return ROUTES[id].pattern;
}

/**
 * Navigation observes the server-authoritative session query directly. TanStack
 * Query retains the last successful payload during a background refetch, so an
 * authorized Admin destination stays stable without rendering before the first
 * successful authorization response.
 */
export function useAdminNavigationAccess(): boolean {
  const session = useQuery(sessionQueryOptions());
  return session.data?.can_access_admin === true;
}

export function RouteNav() {
  // The keyed read (runtime-05): a drawer open or another app-state change
  // leaves the nav alone; only the last borrower moves its two detail links.
  const { lastBorrowerId } = useApp('lastBorrowerId');
  const canAccessAdmin = useAdminNavigationAccess();
  const canReadLedger = useAuditLedgerAccess();
  const queryClient = useQueryClient();
  // Docked (sticky) only while the measured nav is at most a sixth of the
  // scroller (hooks/useRouteNavDock, report 12.4 #5).
  const navRef = useRef<HTMLElement>(null);
  useRouteNavDock(navRef);
  // Intent (hover / focus) preloads the route chunk. Analytics alone also
  // prefetches its unfiltered hero reads (non-audited aggregates, audit
  // delivery-03), unless the browser asks to save data. Home prefetches no
  // data on hover (its summary read starts Genie phrasing; perf-motion pins
  // that a hover asks the API for nothing), and the queue, borrower and offer
  // paths never do: their reads write VIEW_* audit rows.
  const onIntent = (to: AppPath) => {
    if (to === ROUTES.analytics.pattern && !saveDataRequested()) prefetchRouteData(queryClient, to, '');
    else preloadRouteForPath(to);
  };
  // Admin for administrators; 'Audit' (the ledger) only for a non-admin
  // auditor, in Admin's place, so the nav stays one 57px line at 1440x900 with
  // the Console open (D-audit-reads-c3). Administrators reach the ledger from
  // the rail, the Admin page and the palette.
  const shown = (id: NavigationRouteId) => (
    id === 'admin' ? canAccessAdmin : id === 'auditLedger' ? canReadLedger && !canAccessAdmin : true
  );
  // One list per cluster: a div with an explicit role (as GenieAnswer's
  // lists), which keeps the list and its name in WebKit / VoiceOver and is
  // no redundant <ul role="list">. A cluster with no visible link renders
  // nothing.
  const groups = NAVIGATION_GROUPS.map((group) => ({
    ...group,
    items: group.routes.filter(shown).map((id) => ({ id, to: navTargetFor(id, lastBorrowerId), route: ROUTES[id] })),
  })).filter((group) => group.items.length > 0);
  return (
    <nav ref={navRef} aria-label="Main navigation" className="route-nav">
      {groups.map((group) => (
        <div
          key={group.id}
          className={`route-nav__group${group.id === 'tools' ? ' route-nav__group--end' : ''}`}
          role="list"
          aria-label={group.label}
        >
          {group.items.map((i) => (
            <div key={i.id} className="route-nav__item" role="listitem">
              <NavLink
                to={i.to}
                end={i.to === '/'}
                onMouseEnter={() => onIntent(i.to)}
                onFocus={() => onIntent(i.to)}
                className="route-nav__link"
              >
                <Icon name={i.route.icon} size={12} />
                <span className="route-nav__label">{i.route.navLabel}</span>
              </NavLink>
            </div>
          ))}
        </div>
      ))}
    </nav>
  );
}
