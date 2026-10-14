/**
 * The Lead Queue's opener for the Distribution Board (2026-09-21 audit
 * wow-power-5, the W5d sales-loop deferral [2];
 * deviation:lead-queue-distribution-opener).
 *
 * "Plan distribution" in the bulk toolbar opens the Board on the selected
 * rows. The Board is the default export of DistributionBoard, loaded through
 * React.lazy only when it opens (its own chunk, never the LeadTable chunk);
 * its dry run, commit, 403 and success toast are its own, and the queue's
 * audited reads are only stale-marked by its commit.
 *
 * Who sees it (W5e correction C10): the server plans and commits only for a
 * sales manager or an admin (sales_state_core.require_manager_actor), and a
 * loan officer's scoped roster is non-empty, so `salesTeam.length > 0` is no
 * manager gate. The opener shows only when the signed-in user's OWN roster
 * row has one of those roles. The roster is read from the cache the route
 * already filled (an observer that never fetches), so the toolbar adds no
 * request.
 */
import { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';

const DistributionBoard = lazy(() => import('./DistributionBoard'));

/** DistributeLeadsRequest.borrower_ids' cap (backend/schemas/sales.py). */
export const DISTRIBUTION_MAX_BORROWERS = 500;

const PLANNER_ROLES: ReadonlySet<string> = new Set(['sales_manager', 'admin']);

/** The signed-in user's own roster row is a sales manager's or an admin's. */
export function useCanPlanDistribution(actorEmail: string | null): boolean {
  const roster = useQuery({
    queryKey: queryKeys.salesRoster(),
    queryFn: ({ signal }) => api.salesTeam(signal),
    // The route's roster observer fetches; this one only reads its cache.
    enabled: false,
  });
  const email = actorEmail?.toLowerCase() ?? null;
  if (email === null) return false;
  const own = roster.data?.find((member) => member.active && member.email.toLowerCase() === email);
  return own !== undefined && PLANNER_ROLES.has(own.role);
}

/** The Board on the selected rows, while open. */
export function LeadDistributionBoard({ borrowerIds, onClose }: { borrowerIds: string[] | null; onClose: () => void }) {
  if (borrowerIds === null) return null;
  return (
    <Suspense fallback={null}>
      <DistributionBoard borrowerIds={borrowerIds} onClose={onClose} />
    </Suspense>
  );
}
