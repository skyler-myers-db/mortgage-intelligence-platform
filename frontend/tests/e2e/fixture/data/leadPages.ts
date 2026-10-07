/**
 * Paged Lead Queue fixtures (W5c, D-audit-reads-a / tables-02). Scenario
 * helpers only: nothing here is appended to registry.ts; a spec registers
 * what it proves per test.
 *
 *   - `PAGED_QUEUE`: a 1,284-row ranked view, the 500-row queue
 *     (data/leadQueue.ts) and then synthetic masked ids.
 *   - `serverOrder`: the rows in the order the server sorts a view
 *     (backend databricks_lead_order.py): the column NULLS LAST, then rank,
 *     then the borrower id; rank is the rows' own order.
 *   - `registerPagedQueue`: GET /api/leads served 500 rows at a time behind
 *     a signed-looking cursor, with the view id, page index and next-cursor
 *     headers, recording every read; a page can fail once (409, 422, 500),
 *     and the server can be unable to page (X-Lead-Paging: unavailable).
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { LeadSummary } from '../../../../src/types';
import { json, type FixtureReply, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS, maskedBorrowerId } from './borrowers';
import { LEAD_QUEUE_500 } from './leadQueue';

export const PAGE_SIZE = 500;
export const PAGED_TOTAL = 1_284;
/** The first view's id; a restarted view (after a 409 / 422) gets the next one. */
export const PAGED_VIEW_ID = '5a0e1d2c3b4a5968776655443322110';

function pagedSynthetic(index: number): LeadSummary {
  const template = LEADS[index % LEADS.length];
  const borrowerId = maskedBorrowerId(index);
  return {
    ...template,
    borrower_id: borrowerId,
    display_name: `Owner ${borrowerId.slice(2, 8)}`,
    clip: `clip_demo_${borrowerId.slice(2, 8).toLowerCase()}`,
    opportunity_score: Math.max(30, 48 - Math.floor((index - LEAD_QUEUE_500.length) / 60)),
    equity_estimate: 40_000 + ((index * 7_919) % 600_000),
    approval_status: 'pending',
    outreach_status: 'none',
  };
}

/** Ranked order, as page 0 of the rank view returns it. */
export const PAGED_QUEUE: readonly LeadSummary[] = Array.from(
  { length: PAGED_TOTAL },
  (_, index) => (index < LEAD_QUEUE_500.length ? LEAD_QUEUE_500[index] : pagedSynthetic(index)),
);

const SORT_COLUMN: Record<string, (lead: LeadSummary) => number | null | undefined> = {
  score: (lead) => lead.opportunity_score,
  equity: (lead) => lead.equity_estimate,
  rate: (lead) => lead.rate_spread_bps,
  confidence: (lead) => lead.confidence,
};

/** The view's order for `?sort=&sort_dir=` (rank when absent). */
export function serverOrder(rows: readonly LeadSummary[], sort: string | null, dir: string | null): LeadSummary[] {
  const column = sort ? SORT_COLUMN[sort] : undefined;
  if (!column) return [...rows];
  const sign = dir === 'asc' ? 1 : -1;
  return rows
    .map((lead, rank) => ({ lead, rank, value: column(lead) }))
    .sort((a, b) => {
      const aNull = a.value === null || a.value === undefined;
      const bNull = b.value === null || b.value === undefined;
      if (aNull !== bNull) return aNull ? 1 : -1;
      if (!aNull && !bNull && a.value !== b.value) return ((a.value as number) - (b.value as number)) * sign;
      return a.rank - b.rank || a.lead.borrower_id.localeCompare(b.lead.borrower_id);
    })
    .map(({ lead }) => lead);
}

export interface PagedRead {
  page: number;
  cursor: string | null;
  sort: string | null;
  dir: string | null;
  status: number;
}

export interface PagedQueueOptions {
  rows?: readonly LeadSummary[];
  /** Answer the first read of this page index with this status and detail. */
  failOnce?: { page: number; status: number; detail: string };
  /** The server has no cursor key: page 0 only, `X-Lead-Paging: unavailable`. */
  unavailable?: boolean;
}

export interface PagedQueue {
  readonly reads: PagedRead[];
  /** The view id page 0 answered most recently. */
  viewId(): string;
}

function cursorFor(page: number, viewSeq: number): string {
  return `v${viewSeq}p${page}.sig${viewSeq}`;
}

function pageOf(cursor: string | null): number {
  if (cursor === null) return 0;
  const match = /^v\d+p(\d+)\.sig\d+$/.exec(cursor);
  return match ? Number(match[1]) : -1;
}

export function pagedReply(
  rows: readonly LeadSummary[],
  query: URLSearchParams,
  viewId: string,
  viewSeq: number,
  options: { unavailable?: boolean } = {},
): FixtureReply<LeadSummary[] | { detail: string }> {
  const cursor = query.get('cursor');
  const page = pageOf(cursor);
  if (page < 0) return json({ detail: 'lead_view_cursor_invalid' }, { status: 422 });
  const ordered = serverOrder(rows, query.get('sort'), query.get('sort_dir'));
  const slice = ordered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const more = !options.unavailable && (page + 1) * PAGE_SIZE < ordered.length;
  const headers: Record<string, string> = {
    'X-Total-Matching': String(ordered.length),
    'X-Ranked-Matching': String(ordered.length),
    'X-Returned-Rows': String(slice.length),
    'X-Lead-View-Id': viewId,
    'X-Page-Index': String(page),
  };
  if (more) headers['X-Next-Cursor'] = cursorFor(page + 1, viewSeq);
  if (options.unavailable) headers['X-Lead-Paging'] = 'unavailable';
  return json<LeadSummary[]>(slice, { headers });
}

export function registerPagedQueue(mockApi: MockApi, options: PagedQueueOptions = {}): PagedQueue {
  const rows = options.rows ?? PAGED_QUEUE;
  const reads: PagedRead[] = [];
  let viewSeq = 0;
  let failed = false;
  const viewId = () => `${PAGED_VIEW_ID}${viewSeq.toString(16)}`;
  mockApi.register<LeadSummary[] | { detail: string }>('GET', '/api/leads', ({ query }: FixtureRequest) => {
    const cursor = query.get('cursor');
    const page = pageOf(cursor);
    if (cursor === null) viewSeq += 1;
    const read: PagedRead = { page, cursor, sort: query.get('sort'), dir: query.get('sort_dir'), status: 200 };
    reads.push(read);
    const fail = options.failOnce;
    if (fail && !failed && page === fail.page) {
      failed = true;
      read.status = fail.status;
      return json({ detail: fail.detail }, { status: fail.status });
    }
    return pagedReply(rows, query, viewId(), viewSeq, { unavailable: options.unavailable });
  });
  return { reads, viewId };
}
