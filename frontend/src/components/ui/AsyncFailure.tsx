import { describeApiError } from '../../lib/describeApiError';
import { copyLink } from '../../lib/copyLink';
import { useOptionalHealth } from '../HealthProvider';
import { degradedDependency, friendlyDependencyName, isBanneredOutage } from '../healthRecovery';
import { Countdown, useSecondsUntil } from './RetryClock';
import './AsyncState.css';

/**
 * The failure half of AsyncStatus (audit 2026-09-21 `states-04`, `states-03`
 * part a, `states-08`), loaded on demand, only once a failure is on screen:
 * the error vocabulary, the banner rule's copy, the countdown and this markup
 * stay out of every route's natural-load closure.
 *
 *   - the bannered outage (isBanneredOutage): a calm role=status line, "This
 *     panel reloads when the <dep> reconnects", with a secondary Retry; never
 *     an alert under a banner that already says it;
 *   - any other failure: a role=alert callout with the describeApiError title
 *     and body, ONE action, and the support reference as a copyable mono
 *     chip. A 429's Retry is aria-disabled (never native disabled) while it
 *     counts the server's wait down, and live at zero.
 *
 * DescribedError reads `describeApiError` from this same chunk.
 */
export { describeApiError };

export interface AsyncFailureProps {
  error: Error;
  subject: string;
  onRetry: () => void;
  /** The 422 action; without it an invalid-filter error offers no button. */
  onClearFilters?: () => void;
  errorUpdatedAt: number | null;
}

export function AsyncFailure({ error, subject, onRetry, onClearFilters, errorUpdatedAt }: AsyncFailureProps) {
  const healthCtx = useOptionalHealth();
  const health = healthCtx?.health ?? null;
  if (isBanneredOutage(error, health, healthCtx?.connection ?? 'online')) {
    return (
      <div className="status-callout" role="status" aria-live="polite" data-async-status="bannered">
        <strong>{subject}</strong> This panel reloads when the {friendlyDependencyName(degradedDependency(health) ?? '')} reconnects.{' '}
        <CountdownRetry until={0} subject={subject} onRetry={onRetry} />
      </div>
    );
  }
  const description = describeApiError(error, { subject });
  if (description.kind === 'aborted') return null;
  const tone = description.tone === 'warning' ? 'status-callout--warning' : 'status-callout--danger';
  const waitUntil = description.kind === 'rate_limited' && description.retryAfterMs !== null && errorUpdatedAt !== null
    ? errorUpdatedAt + description.retryAfterMs
    : 0;
  return (
    <div className={`status-callout ${tone}`} role="alert" data-async-status={description.kind}>
      <span>
        <strong>{description.title}.</strong> {description.body}
      </span>
      {description.action === 'retry' && (
        <CountdownRetry key={waitUntil} until={waitUntil} subject={subject} onRetry={onRetry} />
      )}
      {description.action === 'clear-filters' && onClearFilters && (
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClearFilters} aria-label="Clear the invalid filters">
          Clear filters
        </button>
      )}
      {description.action === 'reload' && (
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => window.location.reload()}>
          Reload
        </button>
      )}
      {description.correlationId && <Reference id={description.correlationId} />}
    </div>
  );
}

/** Retry, aria-disabled until `until` (a 429's wait); `until` 0 means now. */
function CountdownRetry({ until, subject, onRetry }: { until: number; subject: string; onRetry: () => void }) {
  const waiting = useSecondsUntil(until) > 0;
  return (
    <span className="async-status__wait">
      {waiting && (
        <span className="muted">
          Try again in <Countdown until={until} />
        </span>
      )}
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        aria-disabled={waiting || undefined}
        aria-label={`Retry loading ${subject.toLowerCase()}`}
        onClick={waiting ? undefined : onRetry}
      >
        Retry
      </button>
    </span>
  );
}

function Reference({ id }: { id: string }) {
  return (
    <div className="async-status__ref">
      Reference <span className="callout-code" data-testid="async-status-reference">{id}</span>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={() => void copyLink(id, { success: 'Reference copied', failure: 'Copy failed' })}
      >
        Copy
      </button>
    </div>
  );
}
