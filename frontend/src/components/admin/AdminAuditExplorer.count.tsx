/**
 * The explorer's on-demand total (W5d, tables-10;
 * deviation:audit-explorer-controls). The page is snapshot-cursor
 * paginated, so "how many match" is a separate bounded COUNT, and GET
 * /audit/count is a RECORDED read (one
 * VIEW_AUDIT_LEDGER 'count' row). It runs only on a click of 'Count matching
 * rows', for the applied filters and the exclusion; a filter change hides
 * the count until the next click. Never on mount, focus or a poll.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { auditCount, type AuditExplorerQuery } from '../../lib/apiClients/auditExplorer';
import { formatCount } from '../../lib/formatters';
import { queryKeys } from '../../lib/queryKeys';

interface AuditCountProps {
  /** The applied filter set (and exclusion) the count is for. */
  query: AuditExplorerQuery;
  /** Its identity: a change hides the count until the next click. */
  filtersKey: string;
  firstRow: number;
  lastRow: number;
}

export function countSentence(firstRow: number, lastRow: number, count: number, capped: boolean): string {
  const total = capped ? `more than ${formatCount(count)}` : formatCount(count);
  return `Showing rows ${formatCount(firstRow)}–${formatCount(lastRow)} of ${total} matching`;
}

export function AuditMatchingCount({ query, filtersKey, firstRow, lastRow }: AuditCountProps) {
  const [requestedKey, setRequestedKey] = useState<string | null>(null);
  // Any filter change forgets the click, so returning to an earlier filter
  // set never re-reads without a new one (adjusted during render).
  const [seenKey, setSeenKey] = useState(filtersKey);
  if (seenKey !== filtersKey) {
    setSeenKey(filtersKey);
    setRequestedKey(null);
  }
  const requested = requestedKey !== null && requestedKey === filtersKey;
  const count = useQuery({
    queryKey: queryKeys.auditCount(filtersKey),
    queryFn: ({ signal }) => auditCount(query, signal),
    enabled: requested,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const shown = requested && count.data !== undefined;
  return (
    <div className="audit-count" aria-live="polite">
      {shown && count.data ? (
        <span className="muted fs-12" data-testid="audit-count">
          {countSentence(firstRow, lastRow, count.data.count, count.data.capped)}
        </span>
      ) : (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => {
            if (requested) void count.refetch();
            else setRequestedKey(filtersKey);
          }}
          disabled={requested && count.isFetching}
          aria-busy={(requested && count.isFetching) || undefined}
        >
          Count matching rows
        </button>
      )}
      {requested && count.isError && !count.isFetching && (
        <span className="muted fs-12" role="status">The count could not load.</span>
      )}
    </div>
  );
}
