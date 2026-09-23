import { useQuery } from '@tanstack/react-query';
import { NavLink } from 'react-router';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import { preloadRouteForPath } from '../../lib/routePreloaders';
import {
  NAV_ROUTE_IDS,
  ROUTES,
  borrowerPath,
  offerPath,
  type AppPath,
  type NavRouteId,
} from '../../lib/routeMeta';
import type { SessionResponse } from '../../types';

/**
 * Secondary route nav — chip strip matching the prototype's `.filter` /
 * `.layout-tabs` styling. The prototype is a single screen; our app splits
 * Module 0 across eight routes for the linear user flow (portfolio → segments →
 * leads → borrower → offer → …). This sub-nav is the only deviation from the
 * prototype's composition, and uses prototype-native chip styling so it still
 * reads as part of the same design system.
 */

/**
 * Where a nav chip points. Labels and icons come from the route registry
 * (lib/routeMeta, audit shell-08); only the two detail destinations are
 * resolved here, to the last borrower the actor opened.
 */
function navTargetFor(id: NavRouteId, lastBorrowerId: string | null): AppPath {
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
  const items = NAV_ROUTE_IDS
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
            className={({ isActive }) => `filter ${isActive ? 'is-active' : ''}`}
          >
            <Icon name={i.route.icon} size={12} />
            <span className="filter__value">{i.route.navLabel}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
