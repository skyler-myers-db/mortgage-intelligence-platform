import { Timestamp } from './Timestamp';
import './FetchedAt.css';

/**
 * FetchedAt — "Fetched 3 min ago · Refresh" (audit 2026-09-21 `states-09`).
 *
 * Focus refetch is off app-wide because many reads write VIEW_* audit rows,
 * so a view left open for an hour looked live. This says how old the view is
 * (the query's own fetch time, not the gold table's refresh time) and offers
 * the one explicit way to re-read it: Refresh calls the query's manualRetry,
 * a user action, so on the Lead Queue it is exactly one GET /leads and one
 * VIEW_LEADS row, today's per-page semantic. Never fired automatically.
 *
 * `.fetched-at` is a declared extension (design_files has no freshness
 * control); colocated CSS, lazy with the routes that show it.
 */
interface FetchedAtProps {
  /** When the view's rows were fetched (epoch ms); null renders nothing. */
  at: number | null;
  /** What Refresh re-reads, for its accessible name: "ranked borrowers". */
  subject: string;
  isFetching: boolean;
  onRefresh: () => void;
}

export function FetchedAt({ at, subject, isFetching, onRefresh }: FetchedAtProps) {
  if (at === null) return null;
  return (
    <span className="fetched-at" data-testid="fetched-at">
      <span className="fetched-at__label">
        Fetched <Timestamp value={at} relativeStyle="narrow" />
      </span>
      <RefreshButton subject={subject} isFetching={isFetching} onRefresh={onRefresh} />
    </span>
  );
}

/** aria-disabled (never native disabled) while a read is in flight. */
export function RefreshButton({
  subject,
  isFetching,
  onRefresh,
  label = 'Refresh',
}: {
  subject: string;
  isFetching: boolean;
  onRefresh: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      className="btn btn--ghost btn--sm"
      aria-label={`Refresh ${subject}`}
      aria-disabled={isFetching || undefined}
      onClick={isFetching ? undefined : onRefresh}
    >
      {isFetching ? 'Refreshing…' : label}
    </button>
  );
}
