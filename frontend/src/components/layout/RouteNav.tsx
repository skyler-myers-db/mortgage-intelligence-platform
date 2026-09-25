import { useQuery } from '@tanstack/react-query';
import { NavLink } from 'react-router';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import { preloadRouteForPath } from '../../lib/routePreloaders';
import {
  NAVIGATION_ROUTE_IDS,
  ROUTES,
  borrowerPath,
  offerPath,
  type AppPath,
  type NavigationRouteId,
} from '../../lib/routeMeta';
import type { SessionResponse } from '../../types';

/**
 * Secondary route nav. APP-ADDED ELEMENT: the prototype is a single screen
 * with a rail plus `.segmented` (design_files/index.html:926-935) and has no
 * route nav; our app splits Module 0 across routes for the linear user flow
 * (portfolio → segments → leads → borrower → offer → …).
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
  const session = useQuery<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });
  return session.data?.can_access_admin === true;
}

export function RouteNav() {
  const { lastBorrowerId } = useApp();
  const canAccessAdmin = useAdminNavigationAccess();
  const items = NAVIGATION_ROUTE_IDS
    .filter((id) => canAccessAdmin || id !== 'admin')
    .map((id) => ({ id, to: navTargetFor(id, lastBorrowerId), route: ROUTES[id] }));
  return (
    <nav aria-label="Main navigation" className="route-nav">
      {items.map((i) => {
        const end = i.to === '/';
        return (
          <NavLink
            key={i.id}
            to={i.to}
            end={end}
            onMouseEnter={() => preloadRouteForPath(i.to)}
            onFocus={() => preloadRouteForPath(i.to)}
            className="route-nav__link"
          >
            <Icon name={i.route.icon} size={12} />
            <span className="route-nav__label">{i.route.navLabel}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
