/**
 * When the shared WarmingUpBlock steps aside for the DegradedBanner, and so
 * when the geography map must say "warming up" in its stage by itself
 * (audit dataviz-04, review round 2).
 *
 * WarmingUpBlock renders nothing while the health poll reports the same
 * dependency down (R6-11: the banner tells the cold-start story once, not
 * three times). A routine resume now reports `warehouse: 'resuming'` (not
 * degraded, so the block renders), but an open breaker or a failed fallback
 * `SELECT 1` still reports `warehouse: 'down'`, and then the block inside the
 * map stage would vanish, leaving a blank country. The
 * map cannot hide its stage behind a banner at the top of the page: it keeps
 * one line of status in the stage whenever the block would render nothing.
 *
 * `warmingBlockDefersToBanner` mirrors WarmingUpBlock's rule exactly;
 * USChoroplethMap.warming.test.tsx renders the real block for every case and
 * fails if the two ever disagree, so a change to the block's rule cannot
 * silently blank the stage again.
 */
import type { HealthPayload } from '../../lib/api';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { computeDegraded, useOptionalHealth } from '../HealthProvider';

/** True when WarmingUpBlock renders nothing for `state` under `health` (see its R6-11 note). */
export function warmingBlockDefersToBanner(health: HealthPayload | null, state: WarmingUpState): boolean {
  if (!computeDegraded(health)) return false;
  const deps = health?.dependencies ?? {};
  const blockDep = (state.dependency ?? '').toLowerCase();
  const banneredDep = deps.warehouse === 'down' ? 'warehouse' : deps.lakebase === 'down' ? 'lakebase' : null;
  return !blockDep || blockDep === banneredDep;
}

/** Whether a WarmingUpBlock for `state` would render nothing under the shared health poll. */
export function useWarmingBlockDefers(state: WarmingUpState | null): boolean {
  const healthCtx = useOptionalHealth();
  return state !== null && healthCtx !== null && warmingBlockDefersToBanner(healthCtx.health, state);
}
