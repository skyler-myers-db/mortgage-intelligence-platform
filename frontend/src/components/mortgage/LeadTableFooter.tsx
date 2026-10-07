import { useState } from 'react';
import { formatCount } from '../../lib/formatters';
import type { LeadTablePaging, LeadTableSortScope, SortKey } from './LeadTable.types';

/** The page size of a server-paged view (backend LEAD_PAGE_SIZE). */
const LEAD_PAGE_SIZE = 500;

export interface LeadTableFooterProps {
  /** Rows the parent loaded (the server's top-ranked window). */
  loadedCount: number;
  totalMatching: number | null;
  truncatedAt: number | null;
  sortKey: SortKey;
  /** The active sort column's header label (LeadTable.columns). */
  sortLabel?: string;
  /** 'server': a warehouse sort ordered the whole view (the paged Lead Queue). */
  sortScope?: LeadTableSortScope;
  /** Rows the table sorted (the loaded rows after the sales overrides). */
  sortedCount: number;
  onResetSort: () => void;
  /** The server-paged view; absent (Segment Intelligence) the footer is unchanged. */
  paging?: LeadTablePaging | null;
}

/**
 * The ranked-borrower table's `.surface__ft` (the loaded count, the cap and
 * the sort scope). Moved verbatim out of LeadTable.tsx (audit tables-05 step
 * 0); only the values it reads became props.
 *
 * A server-paged view (the Lead Queue, D-audit-reads-a; a declared BEM
 * extension of the prototype's static count line, design_files/Module 0
 * Prototype.html:2074 under the fixed 480px scroller at :2009;
 * deviation:lead-queue-load-next) drops "capped at", names a server sort,
 * and offers ONE explicit "Load next" per click: never a scroll load (that
 * traps keyboard users away from here), aria-disabled (never `disabled`,
 * which would drop focus) while a page is on the wire. A polite status line
 * announces each loaded page; at ten pages, or when the server cannot page,
 * it says to narrow the filters instead.
 */
export function LeadTableFooter({
  loadedCount,
  totalMatching,
  truncatedAt,
  sortKey,
  sortLabel = sortKey,
  sortScope = 'loaded',
  sortedCount,
  onResetSort,
  paging = null,
}: LeadTableFooterProps) {
  // The shell's compile posture (audit runtime-04, cut 5), as in LeadTableHeader.
  'use no memo';

  // "Loaded k more": derived when the SAME view gains a page (no effect, no
  // timer). A new view (a filter, sort or Refresh) announces nothing here.
  const viewId = paging?.viewId ?? null;
  const pages = paging?.pagesLoaded ?? 0;
  const [announced, setAnnounced] = useState({ viewId, pages, count: loadedCount, text: '' });
  if (announced.viewId !== viewId || announced.pages !== pages) {
    const grew = viewId !== null && announced.viewId === viewId && pages > announced.pages;
    setAnnounced({
      viewId,
      pages,
      count: loadedCount,
      text: grew
        ? `Loaded ${formatCount(loadedCount - announced.count)} more · showing ${formatCount(loadedCount)}`
          + (totalMatching !== null ? ` of ${formatCount(totalMatching)}` : '')
        : '',
    });
  }
  const moreExist = totalMatching !== null && totalMatching > loadedCount;
  const serverSorted = paging !== null && sortScope === 'server';
  const nextCount = totalMatching !== null ? Math.min(LEAD_PAGE_SIZE, totalMatching - loadedCount) : LEAD_PAGE_SIZE;
  const canLoadNext = paging !== null && paging.hasMore && !paging.capped && !paging.unavailable;

  return (
    <div className="surface__ft">
      Showing {formatCount(loadedCount)} ranked borrower{loadedCount === 1 ? '' : 's'}
      {totalMatching !== null && <>{' '}of {formatCount(totalMatching)} total matching filters</>}
      {paging === null && truncatedAt !== null && totalMatching !== null && totalMatching > loadedCount && (
        <span className="muted"> · capped at {formatCount(truncatedAt)}</span>
      )}
      {/* Audit tables-02: sorting reorders only the rows already loaded
          (the server returns the top-ranked window) and nothing said so;
          no header could reach toggleSort('rank') either. */}
      {sortKey !== 'rank' && (
        <>
          {serverSorted ? (
            <span data-testid="lead-sort-scope"> · sorted by {sortLabel}</span>
          ) : (
            <span data-testid="lead-sort-scope">
              · sorted within the loaded {formatCount(sortedCount)}
              {totalMatching !== null && totalMatching > sortedCount
                ? `, not across all ${formatCount(totalMatching)} matching`
                : ''}
            </span>
          )}
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
      {paging && (
        <>
          {paging.queueUpdated && (
            <span className="muted" data-testid="lead-paging-queue-updated"> · Queue updated: reloaded from the top</span>
          )}
          {canLoadNext && !paging.nextError && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              aria-disabled={paging.fetchingNext ? 'true' : undefined}
              onClick={() => {
                if (!paging.fetchingNext) paging.loadNext();
              }}
              data-testid="lead-load-next"
            >
              Load next {formatCount(nextCount)}
            </button>
          )}
          {paging.nextError && (
            <span className="text-danger" role="alert" data-testid="lead-load-next-error">
              {' '}Couldn&apos;t load the next {formatCount(nextCount)} ·{' '}
              <button type="button" className="btn btn--ghost btn--sm" onClick={paging.retryNext}>
                Retry
              </button>
            </span>
          )}
          {(paging.capped || paging.unavailable) && moreExist && (
            <span className="muted" data-testid="lead-paging-narrow"> · Narrow the filters to see more</span>
          )}
          <span className="sr-only" role="status" aria-live="polite" data-testid="lead-paging-status">
            {announced.text}
          </span>
        </>
      )}
    </div>
  );
}
