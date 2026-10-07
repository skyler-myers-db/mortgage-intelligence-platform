/**
 * The Lead Queue's PAGED `/api/leads` read (D-audit-reads-a, tables-02):
 * one page of a server view, page 0 without a cursor and each later page
 * behind the signed `X-Next-Cursor` the previous page answered.
 *
 * Deliberately NOT spread into `api` (decision 4): the api clients ride the
 * initial chunk, and only the Lead Queue route reads pages, so this module is
 * reached only through routes/lead-queue.pages.ts (the route's own chunk).
 * `api.leadsPage` keeps its contract for every other caller.
 *
 * A page past 0 resends the IDENTICAL request-level parameters (the server
 * binds the cursor to their fingerprint) and never a limit. The cursor is
 * held only in the query cache's page params: never in the browser URL, the
 * router state, a query key, a log line or a toast.
 */
import type { LeadSummary } from '../../types';
import type { GrowthAgentCohortVerification } from '../apiTypes';
import { _growthAgentProofFromLocation, _verifyGrowthAgentCohort, getJsonWithHeaders } from '../apiTransport';
import type { LeadsRequest } from '../leadsQuery';
import { dataRefreshedAtHeader } from './headers';
import { leadsQueryParams } from './leads';

/** The server sort tokens (backend/schemas/lead_query.py SortParam). */
export type LeadServerSort = 'rank' | 'score' | 'equity' | 'rate' | 'confidence';

export interface LeadServerOrder {
  sort: LeadServerSort;
  /** Ignored (and never sent) for rank. */
  dir: 'asc' | 'desc';
}

/** One served page of a Lead Queue view. */
export interface LeadsPagedPage {
  leads: LeadSummary[];
  totalMatching: number | null;
  rankedMatching: number | null;
  returnedRows: number | null;
  truncatedAt: number | null;
  dataRefreshedAt: string | null;
  /** Page 0 only: a later page carries no cohort headers (the view shows page 0's). */
  growthAgentVerification: GrowthAgentCohortVerification | null;
  viewId: string | null;
  pageIndex: number;
  nextCursor: string | null;
  /** `unavailable`: the server could not mint a cursor (no key); the view is page 0 only. */
  paging: 'ok' | 'unavailable';
}

const VIEW_ID_RE = /^[0-9a-f]{32}$/;
const CURSOR_RE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

function headerNumber(headers: Headers, name: string): number | null {
  const value = headers.get(name);
  return value ? Number(value) : null;
}

export async function fetchLeadsPage(
  request: LeadsRequest,
  order: LeadServerOrder,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<LeadsPagedPage> {
  const opts = request.opts ?? {};
  const growthAgentProof = opts.growthAgentProof === undefined ? _growthAgentProofFromLocation() : opts.growthAgentProof;
  // `opts.limit` never rides a paged read: the server pages only its default size.
  const params = leadsQueryParams(request.segment, request.geo, { ...opts, limit: undefined }, growthAgentProof);
  if (order.sort !== 'rank') {
    params.set('sort', order.sort);
    params.set('sort_dir', order.dir);
  }
  if (cursor !== null) params.set('cursor', cursor);
  const qs = params.toString();
  const { data, headers } = await getJsonWithHeaders<LeadSummary[]>(qs ? `/api/leads?${qs}` : '/api/leads', signal);
  const viewId = headers.get('X-Lead-View-Id');
  const nextCursor = headers.get('X-Next-Cursor');
  return {
    leads: data,
    totalMatching: headerNumber(headers, 'X-Total-Matching'),
    rankedMatching: headerNumber(headers, 'X-Ranked-Matching'),
    returnedRows: headerNumber(headers, 'X-Returned-Rows'),
    truncatedAt: headerNumber(headers, 'X-Truncated-At'),
    dataRefreshedAt: dataRefreshedAtHeader(headers.get('X-Data-Refreshed-At')),
    growthAgentVerification: cursor === null && growthAgentProof
      ? await _verifyGrowthAgentCohort(headers, growthAgentProof)
      : null,
    viewId: viewId && VIEW_ID_RE.test(viewId) ? viewId : null,
    pageIndex: Number(headers.get('X-Page-Index') ?? 0) || 0,
    nextCursor: nextCursor && CURSOR_RE.test(nextCursor) ? nextCursor : null,
    paging: headers.get('X-Lead-Paging') === 'unavailable' ? 'unavailable' : 'ok',
  };
}
