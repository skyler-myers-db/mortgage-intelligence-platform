import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { computeDegraded, useOptionalHealth } from '../HealthProvider';

/**
 * WarmingUpBlock — shared presentational component for the cold-start
 * retry story. Renders inside any surface while the underlying fetcher
 * is in a `503 retryable` loop driven by `useWarmingUpRetry`.
 *
 * Copy anchors (from product):
 *   - warming_up   → "Databricks SQL warehouses auto-suspend when idle.
 *                    It takes ~30 seconds to warm up. Retrying…"
 *   - breaker_open → "Circuit breaker cooling down. Probing again in 30s."
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

const BODY_WARMING_UP =
  'Databricks SQL warehouses auto-suspend when idle. It takes ~30 seconds to warm up. Retrying automatically…';
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
  // If health is already reporting the dependency as down, the banner
  // is already telling the story — suppress the route-level duplicate
  // so the page doesn't stack three cold-start affordances. We only
  // suppress when the block's dependency matches the degraded one
  // (warehouse/lakebase mapping) so a genuine per-route warm-up still
  // shows while an unrelated dep is down.
  const healthCtx = useOptionalHealth();
  if (healthCtx && computeDegraded(healthCtx.health)) {
    const deps = healthCtx.health?.dependencies ?? {};
    const blockDep = (state.dependency ?? '').toLowerCase();
    const banneredDep =
      deps.warehouse === 'down'
        ? 'warehouse'
        : deps.lakebase === 'down'
          ? 'lakebase'
          : null;
    // If the banner covers the same dep (or the block didn't name one),
    // the banner's copy is the single source of truth — drop the block.
    if (!blockDep || blockDep === banneredDep) {
      return null;
    }
  }
  const padding = compact ? '10px 12px' : 'var(--sp-3) var(--sp-4)';
  return (
    <div
      className="surface"
      role="status"
      aria-live="polite"
      data-testid="warming-up-block"
      style={{ marginBottom: compact ? 0 : 'var(--gap-grid)' }}
    >
      <div
        className="surface__body"
        style={{
          padding,
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sp-2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Chip variant="neutral" icon="bolt">
            {state.label}
          </Chip>
          <span
            className="mono muted"
            style={{ fontSize: 11 }}
            data-testid="warming-up-attempt"
          >
            (attempt {state.attempt} of {state.maxAttempts})
          </span>
        </div>
        {title && (
          <div className="h-4" style={{ marginTop: 2 }}>
            {title}
          </div>
        )}
        <p className="body muted" style={{ margin: 0, fontSize: 12 }}>
          {body ?? defaultBodyFor(state.label)}
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Icon name="db" size={11} />
          <span className="muted" style={{ fontSize: 10 }}>
            {state.correlationId
              ? `correlation_id: ${state.correlationId}`
              : cadenceFor(state.label)}
          </span>
        </div>
      </div>
    </div>
  );
}
