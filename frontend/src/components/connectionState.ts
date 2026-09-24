import type { QueryClient } from '@tanstack/react-query';
import { clientFailureReason } from '../lib/apiTransport';

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
 * MOUNTED queries whose last attempt never reached the server. Same narrowing
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
    predicate: (query) => {
      const reason = clientFailureReason(query.state.error);
      return reason === 'unreachable' || reason === 'offline';
    },
  });
}
