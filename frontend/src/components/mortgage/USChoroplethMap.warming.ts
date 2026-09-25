/**
 * When the shared WarmingUpBlock steps aside for the DegradedBanner, and so
 * when the geography map must say "warming up" in its stage by itself
 * (audit dataviz-04, review round 2).
 *
 * WarmingUpBlock renders nothing while the banner already tells its
 * dependency's story (R6-11: the cold-start story is told once, not three
 * times). A routine resume reports `warehouse: 'resuming'` (no banner, so the
 * block renders), but an open breaker or a failed fallback `SELECT 1` still
 * reports `warehouse: 'down'`, and then the block inside the map stage would
 * vanish, leaving a blank country. The map cannot hide its stage behind a
 * banner at the top of the page: it keeps one line of status in the stage
 * whenever the block would render nothing.
 *
 * Both read ONE rule, healthRecovery.blockDefersToBanner (audit states-03
 * part a). USChoroplethMap.warming.test.tsx renders the real block for every
 * case and fails if the two ever disagree, so a change to the rule cannot
 * silently blank the stage again.
 */
import type { HealthPayload } from '../../lib/api';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { useOptionalHealth } from '../HealthProvider';
import type { ConnectionStatus } from '../connectionState';
import { blockDefersToBanner } from '../healthRecovery';

/** True when WarmingUpBlock renders nothing for `state` under `health` (see its R6-11 note). */
export function warmingBlockDefersToBanner(
  health: HealthPayload | null,
  state: WarmingUpState,
  connection: ConnectionStatus = 'online',
): boolean {
  return blockDefersToBanner(state.dependency, health, connection);
}

/** Whether a WarmingUpBlock for `state` would render nothing under the shared health poll. */
export function useWarmingBlockDefers(state: WarmingUpState | null): boolean {
  const healthCtx = useOptionalHealth();
  return state !== null
    && healthCtx !== null
    && warmingBlockDefersToBanner(healthCtx.health, state, healthCtx.connection ?? 'online');
}
