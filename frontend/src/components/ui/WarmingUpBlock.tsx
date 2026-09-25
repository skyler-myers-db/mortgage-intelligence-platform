import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { useOptionalHealth } from '../HealthProvider';
import { blockDefersToBanner } from '../healthRecovery';

/**
 * WarmingUpBlock — shared presentational component for the cold-start
 * retry story. Renders inside any surface while the underlying fetcher
 * is in a `503 retryable` loop driven by `useWarmingUpRetry`.
 *
 * Copy anchors (from product):
 *   - warming_up   → `WAREHOUSE_WARMING_BODY`: serverless-accurate, with no
 *                    30 s or 60 s claim (audit delivery-01). A routine resume
 *                    is 2–6 s and shows only as the calm topbar pill; this
 *                    block appears when one is taking longer than that.
 *   - breaker_open → "Circuit breaker cooling down. Probing again in 30s."
 *                    (the 30 s breaker cool-down is real)
 *
 * The attempt counter renders as "(attempt N of M)" so the operator
 * can see forward progress. Optional `title` slot lets per-page use
 * sites name the specific resource ("Loading B-102FL7THC6Q3L…").
 */

interface WarmingUpBlockProps {
  state: WarmingUpState;
  /** Optional title string — e.g. "Loading B-102FL7THC6Q3L…". */
  title?: string;
  /** Optional override for the body copy. */
  body?: string;
  /** Compact mode — thinner padding for in-tile use (Home KPI row). */
  compact?: boolean;
}

/**
 * The one warming-up sentence for every surface that waits on the warehouse
 * (this block, and the Offer Orchestrator / Borrower 360 warming ledes).
 */
export const WAREHOUSE_WARMING_BODY =
  'Databricks SQL warehouses auto-suspend when idle. A serverless resume usually takes 2–6 seconds; this one is taking longer. Retrying automatically…';
const BODY_WARMING_UP = WAREHOUSE_WARMING_BODY;
const BODY_BREAKER_OPEN =
  'The backend circuit breaker tripped after repeated failures. Pausing 30 seconds for the breaker to half-open, then probing again.';

/** Pick the default body copy based on the retry plan's label. */
function defaultBodyFor(label: string): string {
  return label.toLowerCase().includes('circuit breaker')
    ? BODY_BREAKER_OPEN
    : BODY_WARMING_UP;
}

/** Footer cadence copy — keeps the "every Ns" readout honest across reasons. */
function cadenceFor(label: string): string {
  return label.toLowerCase().includes('circuit breaker')
    ? 'Retrying automatically every 30 seconds.'
    : 'Retrying automatically every 5 seconds.';
}

export function WarmingUpBlock({
  state,
  title,
  body,
  compact,
}: WarmingUpBlockProps) {
  // R6-11 (2026-04-23): on a deep-link cold-start the DegradedBanner,
  // footprint-fallback chip, and this per-route block all fire at once.
  // If the banner is already telling this dependency's story, suppress the
  // route-level duplicate so the page doesn't stack three cold-start
  // affordances. One shared rule decides (healthRecovery.blockDefersToBanner,
  // audit states-03 a): the block steps aside exactly when the banner shows
  // for its dependency (or it named none), so a genuine per-route warm-up
  // still shows while an unrelated dependency is down.
  const healthCtx = useOptionalHealth();
  if (healthCtx && blockDefersToBanner(state.dependency, healthCtx.health, healthCtx.connection ?? 'online')) {
    return null;
  }
  return (
    <div
      className={`surface warming-block ${compact ? 'warming-block--compact' : ''}`}
      role="status"
      aria-live="polite"
      data-testid="warming-up-block"
    >
      <div
        className={`surface__body warming-block__body ${compact ? 'warming-block__body--compact' : ''}`}
      >
        <div className="warming-block__head">
          <Chip variant="neutral" icon="bolt">
            {state.label}
          </Chip>
          <span
            className="mono muted warming-block__attempt"
            data-testid="warming-up-attempt"
          >
            (attempt {state.attempt} of {state.maxAttempts})
          </span>
        </div>
        {title && (
          // A caption of this status message, not a section heading: the
          // block comes and goes with each retry inside whatever surface is
          // loading, so it stays out of the heading outline (a11y-03).
          <p className="h-4 warming-block__title">
            {title}
          </p>
        )}
        <p className="body muted warming-block__copy">
          {body ?? defaultBodyFor(state.label)}
        </p>
        <div className="warming-block__footer">
          <Icon name="db" size={11} />
          <span className="muted warming-block__meta">
            {state.correlationId
              ? `correlation_id: ${state.correlationId}`
              : cadenceFor(state.label)}
          </span>
        </div>
      </div>
    </div>
  );
}
