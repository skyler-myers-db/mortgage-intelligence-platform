import type { ReactNode } from 'react';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { describeApiError, type ApiErrorDescription } from '../../lib/describeApiError';
import { copyLink } from '../../lib/copyLink';
import { useOptionalHealth } from '../HealthProvider';
import { degradedDependency, friendlyDependencyName, isBanneredOutage } from '../healthRecovery';
import { Countdown, useSecondsUntil } from './RetryClock';
import { WarmingUpBlock } from './WarmingUpBlock';
import './AsyncState.css';

/**
 * AsyncState / AsyncStatus — the one loading / warming / error / empty
 * surface for a query (audit 2026-09-21 `states-04` slice 2, `states-03`
 * part a, `states-v2`, `states-08` part 3). Routes used to hand-roll each
 * state with raw error text; this renders the shared vocabulary:
 *
 *   AsyncStatus, at most one of:
 *     1. warming with no data yet   -> WarmingUpBlock (it steps aside for
 *                                      the banner by itself);
 *     2. the bannered outage        -> a calm role=status line: "This panel
 *                                      reloads when the <dep> reconnects",
 *                                      plus a secondary Retry. Never an alert
 *                                      under a banner that already says it;
 *     3. any other failure          -> a role=alert callout with the
 *                                      describeApiError title and body, ONE
 *                                      action, and the support reference as
 *                                      a copyable mono chip. A 429's Retry
 *                                      is aria-disabled while it counts down;
 *     4. nothing.
 *   AsyncState adds the data slot: children(data) when there is a payload,
 *   `empty` ONLY for a settled, measured zero, else `loading`.
 */

/** The useWarmingUpRetry result, or an adapter of the same shape. */
export interface AsyncQuery<T> {
  data: T | null;
  warmingUp: WarmingUpState | null;
  error: Error | null;
  manualRetry: () => void;
  isFetching: boolean;
  isPlaceholderData: boolean;
  errorUpdatedAt?: number | null;
}

interface AsyncStatusProps {
  query: AsyncQuery<unknown>;
  /** What this panel shows, sentence case: "Ranked borrowers". */
  subject: string;
  /** The 422 action; without it an invalid-filter error offers no button. */
  onClearFilters?: () => void;
  compact?: boolean;
}

export function AsyncStatus({ query, subject, onClearFilters, compact }: AsyncStatusProps) {
  const healthCtx = useOptionalHealth();
  // TanStack keeps `failureReason` after the last retry, so `warmingUp`
  // outlives the loop; once `error` is set the honest surface is the error.
  const warming = query.error === null ? query.warmingUp : null;
  if (warming && query.data === null) {
    return <WarmingUpBlock state={warming} title={`${subject} loading`} compact={compact} />;
  }
  const { error } = query;
  if (!error) return null;
  const health = healthCtx?.health ?? null;
  const connection = healthCtx?.connection ?? 'online';
  if (isBanneredOutage(error, health, connection)) {
    const dependency = friendlyDependencyName(degradedDependency(health) ?? '');
    return (
      <div className="status-callout" role="status" aria-live="polite" data-async-status="bannered">
        <strong>{subject}</strong> This panel reloads when the {dependency} reconnects.{' '}
        <RetryButton subject={subject} onRetry={query.manualRetry} />
      </div>
    );
  }
  const description = describeApiError(error, { subject });
  if (description.kind === 'aborted') return null;
  return (
    <ErrorCallout
      description={description}
      subject={subject}
      onRetry={query.manualRetry}
      onClearFilters={onClearFilters}
      errorUpdatedAt={query.errorUpdatedAt ?? null}
    />
  );
}

function ErrorCallout({
  description,
  subject,
  onRetry,
  onClearFilters,
  errorUpdatedAt,
}: {
  description: ApiErrorDescription;
  subject: string;
  onRetry: () => void;
  onClearFilters?: () => void;
  errorUpdatedAt: number | null;
}) {
  const tone = description.tone === 'warning' ? 'status-callout--warning' : 'status-callout--danger';
  const waitUntil = description.kind === 'rate_limited' && description.retryAfterMs !== null && errorUpdatedAt !== null
    ? errorUpdatedAt + description.retryAfterMs
    : null;
  return (
    <div className={`status-callout ${tone}`} role="alert" data-async-status={description.kind}>
      <span>
        <strong>{description.title}.</strong> {description.body}
      </span>
      {description.action === 'retry' && (waitUntil === null
        ? <RetryButton subject={subject} onRetry={onRetry} />
        : <CountdownRetry key={waitUntil} until={waitUntil} subject={subject} onRetry={onRetry} />)}
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

function RetryButton({ subject, onRetry }: { subject: string; onRetry: () => void }) {
  return (
    <button type="button" className="btn btn--ghost btn--sm" onClick={onRetry} aria-label={`Retry loading ${subject.toLowerCase()}`}>
      Retry
    </button>
  );
}

/** A 429's Retry: aria-disabled (never native disabled) until the server's wait ends. */
function CountdownRetry({ until, subject, onRetry }: { until: number; subject: string; onRetry: () => void }) {
  const left = useSecondsUntil(until);
  const waiting = left > 0;
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

interface AsyncStateProps<T> {
  query: AsyncQuery<T>;
  subject: string;
  /** The skeleton for a first load. */
  loading: ReactNode;
  /** True for a payload with nothing in it (a measured zero, once settled). */
  isEmpty: (data: T) => boolean;
  empty: ReactNode;
  onClearFilters?: () => void;
  children: (data: T) => ReactNode;
}

export function AsyncState<T>({ query, subject, loading, isEmpty, empty, onClearFilters, children }: AsyncStateProps<T>) {
  const status = <AsyncStatus query={query} subject={subject} onClearFilters={onClearFilters} compact />;
  const { data, error } = query;
  const warming = error === null && query.warmingUp !== null;
  if (data !== null && !isEmpty(data)) {
    return (
      <>
        {status}
        {children(data)}
      </>
    );
  }
  if (data !== null && !query.isPlaceholderData) {
    // EmptyState only for a MEASURED zero: settled, not warming, no error.
    if (error === null && !warming) return <>{empty}</>;
    if (error !== null) return status;
    return <>{loading}</>;
  }
  // No payload yet, or a placeholder zero (the previous filters' rows).
  if (data === null && error !== null) return status;
  // The DegradedBanner can suppress the warming block, so the skeleton keeps
  // the slot honest while warming (states-v1).
  return (
    <>
      {data === null && warming ? status : null}
      {loading}
    </>
  );
}
