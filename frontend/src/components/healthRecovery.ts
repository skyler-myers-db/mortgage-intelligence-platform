import type { QueryClient } from '@tanstack/react-query';
import { ApiError } from '../lib/apiTransport';
import type { HealthPayload } from '../lib/apiTypes';

/**
 * Health recovery — what the shared health poll does on the
 * degraded → healthy edge.
 *
 * Audit 2026-09-21 (`states-03`): the DegradedBanner promised "Live data will
 * resume automatically", but the only recovery refetch in the app was a
 * Home-only effect. A warehouse that is slow to come back (a serverless resume
 * is usually 2–6 s, an outage or a stuck resume much longer) can outlast the
 * per-query retry budget (6 × 5 s), so on every other route panels turned red
 * under a "Reconnecting" banner and STAYED red after recovery until someone
 * clicked Retry. A finished resume (`resuming` → `up`, audit delivery-01) is
 * a recovery too: reads that met the warehouse's 503 while it woke refetch.
 *
 * The refetch is deliberately narrow:
 *   - `type: 'active'` — only queries a mounted component is observing.
 *     Many reads write `VIEW_*` audit rows; re-firing an unmounted audited
 *     read would mint governance evidence for a page nobody is looking at.
 *   - only queries whose error is a retryable 503 `ApiError` naming the
 *     dependency that just recovered. A 403 / 404 / 422 is not an outage and
 *     must not be re-fired; neither must a warehouse failure when it was
 *     Genie that came back.
 */

const DEPENDENCIES = ['warehouse', 'lakebase', 'genie'] as const;

/** Per-dependency filtered state, as tracked by HealthProvider's debounce. */
export interface FilteredDependencyState {
  filtered: 'up' | 'down' | 'resuming' | 'unknown';
}

/**
 * Dependencies that crossed down → up (after the provider's debounce) or
 * resuming → up, or whose circuit breaker went open → closed, between two
 * consecutive health snapshots. A `half_open` breaker is still probing and
 * does not count as recovered.
 */
export function recoveredDependencies(
  priorDeps: Readonly<Record<string, FilteredDependencyState | undefined>>,
  nextDeps: Readonly<Record<string, FilteredDependencyState | undefined>>,
  priorBreakers: HealthPayload['circuit_breakers'],
  nextBreakers: HealthPayload['circuit_breakers'],
): string[] {
  const recovered = new Set<string>();
  for (const dep of DEPENDENCIES) {
    const prior = priorDeps[dep]?.filtered;
    if ((prior === 'down' || prior === 'resuming') && nextDeps[dep]?.filtered === 'up') recovered.add(dep);
  }
  for (const [name, state] of Object.entries(priorBreakers ?? {})) {
    if (state === 'open' && nextBreakers?.[name] === 'closed') recovered.add(name);
  }
  return [...recovered];
}

/** True when `error` is a dependency outage that `recovered` has just ended. */
export function isRecoverableQueryError(error: unknown, recovered: ReadonlySet<string>): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.aborted || !error.retryable || error.status !== 503) return false;
  return error.dependency !== null && recovered.has(error.dependency.toLowerCase());
}

/** Refetch the mounted queries that failed because of a now-recovered dependency. */
export function refetchRecoveredQueries(queryClient: QueryClient, recovered: readonly string[]): void {
  if (recovered.length === 0) return;
  const names = new Set(recovered.map((name) => name.toLowerCase()));
  void queryClient.refetchQueries({
    type: 'active',
    predicate: (query) => isRecoverableQueryError(query.state.error, names),
  });
}
