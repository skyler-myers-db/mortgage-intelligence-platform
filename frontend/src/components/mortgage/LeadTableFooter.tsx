import { formatCount } from '../../lib/formatters';
import type { SortKey } from './LeadTable.types';

export interface LeadTableFooterProps {
  /** Rows the parent loaded (the server's top-ranked window). */
  loadedCount: number;
  totalMatching: number | null;
  truncatedAt: number | null;
  sortKey: SortKey;
  /** Rows the table sorted (the loaded rows after the sales overrides). */
  sortedCount: number;
  onResetSort: () => void;
}

/**
 * The ranked-borrower table's `.surface__ft` (the loaded count, the cap and
 * the sort scope). Moved verbatim out of LeadTable.tsx (audit tables-05 step
 * 0); only the values it reads became props.
 */
export function LeadTableFooter({
  loadedCount,
  totalMatching,
  truncatedAt,
  sortKey,
  sortedCount,
  onResetSort,
}: LeadTableFooterProps) {
  // The shell's compile posture (audit runtime-04, cut 5), as in LeadTableHeader.
  'use no memo';

  return (
    <div className="surface__ft">
      Showing {formatCount(loadedCount)} ranked borrower{loadedCount === 1 ? '' : 's'}
      {totalMatching !== null && <>{' '}of {formatCount(totalMatching)} total matching filters</>}
      {truncatedAt !== null && totalMatching !== null && totalMatching > loadedCount && (
        <span className="muted"> · capped at {formatCount(truncatedAt)}</span>
      )}
      {/* Audit tables-02: sorting reorders only the rows already loaded
          (the server returns the top-ranked window) and nothing said so;
          no header could reach toggleSort('rank') either. */}
      {sortKey !== 'rank' && (
        <>
          <span data-testid="lead-sort-scope">
            · sorted within the loaded {formatCount(sortedCount)}
            {totalMatching !== null && totalMatching > sortedCount
              ? `, not across all ${formatCount(totalMatching)} matching`
              : ''}
          </span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onResetSort}
            data-testid="lead-sort-reset"
          >
            Reset to rank
          </button>
        </>
      )}
    </div>
  );
}
