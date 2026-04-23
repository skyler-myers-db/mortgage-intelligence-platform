import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';

/**
 * WarmingUpBlock — shared presentational component for the cold-start
 * retry story. Renders inside any surface while the underlying fetcher
 * is in a `503 retryable` loop driven by `useWarmingUpRetry`.
 *
 * Copy anchors (from product):
 *   "Databricks SQL warehouses auto-suspend when idle. It takes
 *    ~30 seconds to warm up. Retrying automatically…"
 *
 * The attempt counter renders as "(attempt N of 6)" so the operator
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

const DEFAULT_BODY =
  'Databricks SQL warehouses auto-suspend when idle. It takes ~30 seconds to warm up. Retrying automatically…';

export function WarmingUpBlock({
  state,
  title,
  body,
  compact,
}: WarmingUpBlockProps) {
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
          {body ?? DEFAULT_BODY}
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Icon name="db" size={11} />
          <span className="muted" style={{ fontSize: 10 }}>
            {state.correlationId
              ? `correlation_id: ${state.correlationId}`
              : 'Retrying automatically every 5 seconds.'}
          </span>
        </div>
      </div>
    </div>
  );
}
