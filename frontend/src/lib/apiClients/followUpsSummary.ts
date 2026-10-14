/**
 * The rail's Follow-ups poll (D-shell-deviations-a; audit shell-09,
 * wow-power-3): GET /api/v1/sales/followups/summary, once a minute while the
 * tab is visible.
 *
 * In the initial closure, so it stays tiny: the type is the generated
 * schema's, the fetch is one transport call, and the gate is a pure
 * predicate. The read is audit-free and Lakebase-only on the server; RUM
 * never reports it (lib/rumApiRoute EXCLUDED_TEMPLATES). It runs only for a
 * session that names an actor and is not expired; its errors are silent (the
 * rail item simply does not render). Never persisted.
 */
import { queryOptions } from '@tanstack/react-query';
import type { ApiResponse } from '../../types/api.gen';
import type { ConnectionStatus } from '../../components/connectionState';
import type { SessionResponse } from '../../types';
import { getJson } from '../apiTransport';
import { queryKeys } from '../queryKeys';

export type FollowupSummary = ApiResponse<'FollowupSummary'>;

export const FOLLOW_UPS_POLL_MS = 60_000;

/** Poll only once the session names an actor, and never on an expired session. */
export function followUpsSummaryEnabled(
  session: SessionResponse | undefined,
  connection: ConnectionStatus | undefined,
): boolean {
  return Boolean(session?.actor_email) && connection !== 'session_expired';
}

export function followUpsSummaryQueryOptions(enabled: boolean) {
  return queryOptions<FollowupSummary>({
    queryKey: queryKeys.followUpsSummary(),
    queryFn: ({ signal }) => getJson<FollowupSummary>('/api/v1/sales/followups/summary', signal),
    enabled,
    refetchInterval: FOLLOW_UPS_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
