import type { Query, QueryClient } from '@tanstack/react-query';
import { ApiError, clientFailureReason } from '../lib/apiTransport';
import { isAuditFreeRead } from '../lib/auditFreeReads';

/**
 * A query whose every served page writes an audit row (`meta.auditedPages`:
 * the Lead Queue's paged view, D-audit-reads-a). A recovery never re-reads
 * it: the reader's Refresh does (one page 0, one VIEW_LEADS row).
 */
export function hasAuditedPages(query: Pick<Query, 'meta'>): boolean {
  return query.meta?.auditedPages === true;
}

/** The query's last error, when the network lost it (offline or unreachable). */
function networkLoss(query: Pick<Query, 'state'>): ApiError | null {
  const error = query.state.error;
  if (!(error instanceof ApiError)) return null;
  const reason = clientFailureReason(error);
  return reason === 'offline' || reason === 'unreachable' ? error : null;
}

/**
 * A query whose last attempt never got an HTTP status: fetch() itself
 * rejected (offline, unreachable), which the transport reads as a request that
 * never reached the server (apiFailure.ts). A body lost after its status, a
 * 200 whose connection dropped mid-transfer, reads offline or unreachable too
 * but keeps that status (apiTransport `_readJson`): the server answered it,
 * and an audited read it answered has already written its row.
 */
export function neverAnswered(query: Pick<Query, 'state'>): boolean {
  return networkLoss(query)?.status === null;
}

/**
 * A query the server answered whose body the network then lost: offline or
 * unreachable with a status. Only a reviewed audit-free read
 * (lib/auditFreeReads, per method) may be completed after that; any other
 * method and path count as audited, whose row the answered attempt has
 * already written.
 */
export function answeredAuditFreeLoss(query: Pick<Query, 'state'>): boolean {
  const error = networkLoss(query);
  return error !== null && error.status !== null && isAuditFreeRead(error.path, error.method);
}

/**
 * A query an observer opted out of the reconnect refetch (`refetchOnReconnect:
 * false`): its read runs only on an act (an open, a Retry, Regenerate), never
 * when the network returns. TanStack honours that on a reconnect for that
 * observer; both recoveries honour it for the query, because both are the
 * network coming back. It is also how a query that fans out to several
 * requests stays out of both: they judge a query by the request that failed,
 * and an audited request beside it may have been answered (the Offer detail's
 * snapshot: VIEW_BORROWER answered while RECOMMEND_OFFER never was).
 */
export function optedOutOfReconnect(query: Pick<Query, 'observers'>): boolean {
  return query.observers.some((observer) => observer.options.refetchOnReconnect === false);
}

/**
 * A query declared ONE reviewed audit-free read (`meta.oneAuditFreeRead`):
 * its query function sends a single request, to a route on
 * lib/auditFreeReads, so re-running it writes no row whatever became of the
 * last attempt. It never fans out, so the request that failed is the whole
 * query. Fail-closed: the declaration counts only while the request that
 * failed is on the list (method and path), and connectionState.test.ts pins
 * every query that declares it.
 */
export function isOneAuditFreeRead(query: Pick<Query, 'state' | 'meta'>): boolean {
  if (query.meta?.oneAuditFreeRead !== true) return false;
  const error = networkLoss(query);
  return error !== null && isAuditFreeRead(error.path, error.method);
}

/**
 * The one rule for re-reading a query once the network is back, on a
 * reconnect (lib/queryClient.ts) or a reachable probe after failed ones
 * (refetchUnreachableQueries). Never a paged query whose every served page
 * writes a row. Never one that opted out of the reconnect refetch, unless it
 * is one reviewed audit-free read (isOneAuditFreeRead: a TanStack reconnect
 * still honours its observer's opt-out, so only the "Connection lost"
 * recovery completes it, as before PR #326). Otherwise its last attempt was
 * never answered, so it wrote nothing; or the network lost the body of an
 * answer from a reviewed audit-free read, which writes nothing either. So a
 * panel that writes no audit row loads by itself once the network returns,
 * and an audited read the server answered waits for the reader's Retry.
 */
export function refetchableAfterNetworkLoss(query: Pick<Query, 'state' | 'meta' | 'observers'>): boolean {
  if (hasAuditedPages(query)) return false;
  if (optedOutOfReconnect(query) && !isOneAuditFreeRead(query)) return false;
  return neverAnswered(query) || answeredAuditFreeLoss(query);
}

/**
 * Connection state the shell shows above every page (audit 2026-09-21
 * `states-02`, `shell-v1`), derived by HealthProvider from its one health
 * poll, the browser's online signal and the session store:
 *
 *   - `session_expired` — a request came back 401 (or a confirmed sign-in
 *     redirect). The blocking SessionExpiredDialog owns this; polling stops.
 *   - `offline`         — the browser has no network. Probes stop until it
 *     returns; queries are paused by TanStack's onlineManager.
 *   - `unreachable`     — online, but the last TWO health probes could not
 *     reach the app. One failed probe is a blip; two is "Connection lost".
 *   - `online`          — everything else, including a reachable but degraded
 *     backend, which the dependency banner already explains.
 */
export type ConnectionStatus = 'online' | 'offline' | 'unreachable' | 'session_expired';

/** Consecutive unreachable probes (while online) before "Connection lost". */
export const UNREACHABLE_PROBES_BEFORE_BANNER = 2;

export interface ConnectionTracker {
  status: ConnectionStatus;
  /** Consecutive unreachable probes observed while online. */
  failedProbes: number;
}

export const INITIAL_CONNECTION: ConnectionTracker = { status: 'online', failedProbes: 0 };

export interface ConnectionObservation {
  sessionExpired: boolean;
  online: boolean;
  /** Outcome of a probe that just finished, or null when none ran. */
  probeReachable: boolean | null;
}

/** Pure transition, so the cadence loop stays readable and the rules testable. */
export function nextConnection(prior: ConnectionTracker, seen: ConnectionObservation): ConnectionTracker {
  if (seen.sessionExpired) return { status: 'session_expired', failedProbes: prior.failedProbes };
  if (!seen.online) return { status: 'offline', failedProbes: 0 };
  if (seen.probeReachable === true) return INITIAL_CONNECTION;
  if (seen.probeReachable === false) {
    const failedProbes = prior.failedProbes + 1;
    return {
      status: failedProbes >= UNREACHABLE_PROBES_BEFORE_BANNER ? 'unreachable' : 'online',
      failedProbes,
    };
  }
  // No probe ran (the browser just came back online): nothing is known yet.
  return prior.status === 'offline' ? INITIAL_CONNECTION : prior;
}

/**
 * When a reachable probe follows one or more failed probes (the "Connection
 * lost" banner, or a one-probe blip that never showed it), refetch the
 * MOUNTED queries refetchableAfterNetworkLoss allows: a last attempt that was
 * never answered, or an audit-free answer whose body was lost, and never a
 * query that opted out of the reconnect refetch unless it is one reviewed
 * audit-free read (`meta.oneAuditFreeRead`). Same narrowing
 * as healthRecovery.ts: `type: 'active'` only, because many reads write VIEW_*
 * audit rows and an unmounted audited read must never be re-fired for a page
 * nobody is on. It needs a failed probe first: a read that fails while every
 * probe reaches the app is a path-specific failure, and refetching it on each
 * probe would be a retry loop. Offline -> online needs nothing here: TanStack
 * resumes paused queries itself.
 */
export function refetchUnreachableQueries(queryClient: QueryClient): void {
  void queryClient.refetchQueries({
    type: 'active',
    predicate: refetchableAfterNetworkLoss,
  });
}
