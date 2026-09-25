import type { QueryClient } from '@tanstack/react-query';
import { ApiError } from '../lib/apiTransport';
import type { HealthPayload } from '../lib/apiTypes';
import type { ConnectionStatus } from './connectionState';

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

/** The health fields the dependency banner reads (any health payload shape). */
export type DependencyHealth = Pick<Partial<HealthPayload>, 'status' | 'dependencies' | 'circuit_breakers'>;

/**
 * Map internal Databricks product names → buyer-friendly dependency names.
 * The degraded banner is surfaced to the business buyer (Head of Growth,
 * VP Lending), who doesn't know what "lakebase" or "genie" are. Keep the
 * internal name in `data-degraded-dependency` for ops telemetry; show
 * the friendly name in the visible title.
 */
const FRIENDLY_DEP_NAMES: Record<string, string> = {
  warehouse: 'analytics warehouse',
  lakebase: 'operational database',
  genie: 'AI assistant',
};

export function friendlyDependencyName(dep: string): string {
  return FRIENDLY_DEP_NAMES[dep] ?? dep;
}

export function degradedDependency(health: DependencyHealth | null): string | null {
  if (!health) return null;
  const deps = health.dependencies ?? {};
  if (deps.warehouse === 'down') return 'warehouse';
  if (deps.lakebase === 'down') return 'lakebase';
  if (deps.genie === 'down') return 'genie';
  // Open breaker without a concrete dep ping-down still counts.
  const breakers = health.circuit_breakers ?? {};
  for (const [name, state] of Object.entries(breakers)) {
    if (state === 'open') return name;
  }
  return null;
}

/**
 * The bannered-outage rule (audit states-03 part a): ONE answer, shared by
 * every surface, to "is the DegradedBanner already telling this story?".
 * The banner shows exactly when the connection is online and
 * `degradedDependency` names a dependency, so a surface defers to it only
 * then. A `status: 'degraded'` payload with every dependency up and no open
 * breaker shows no banner, and so hides nothing.
 */

/** True when a WarmingUpBlock for `dependency` should step aside for the banner. */
export function blockDefersToBanner(
  dependency: string | null | undefined,
  health: DependencyHealth | null,
  connection: ConnectionStatus,
): boolean {
  if (connection !== 'online') return false;
  const bannered = degradedDependency(health);
  return bannered !== null && (!dependency || dependency.toLowerCase() === bannered);
}

/**
 * True when `error` is the bannered outage itself: exactly the errors
 * `refetchRecoveredQueries` re-fetches when that dependency comes back, so a
 * panel that says "reloads when … reconnects" is true by construction. A
 * permission_denied 503, a 403/404/409/422/429/500, an abort or another
 * dependency's failure is not, and keeps its own (red) error.
 */
export function isBanneredOutage(
  error: unknown,
  health: DependencyHealth | null,
  connection: ConnectionStatus,
): boolean {
  if (connection !== 'online') return false;
  const bannered = degradedDependency(health);
  return bannered !== null && isRecoverableQueryError(error, new Set([bannered]));
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
