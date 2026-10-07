/**
 * The Lead Queue's paged view (D-audit-reads-a, tables-02 / delivery-02):
 * TanStack's infinite query over GET /api/leads, one served page per explicit
 * action, because every served page writes one VIEW_LEADS audit row.
 *
 * What re-reads /api/leads, and nothing else does:
 *   - a first mount with nothing cached (page 0);
 *   - Load next: exactly one fetchNextPage per click;
 *   - Refresh, the Queue-updated action and the AsyncState Retry:
 *     resetQueries on this exact key, so page 0 ONLY is read again;
 *   - a filter or sort change while mounted: the key changes, and the layout
 *     effect below drops every other paged entry, so the new key is never a
 *     cached (possibly invalidated) view whose pages a key change would
 *     refetch: page 0 only, with the previous rows as the placeholder.
 * Never: a prefetch, a scroll load, a poll, a window focus, a reconnect, a
 * health recovery (healthRecovery / connectionState skip meta.auditedPages),
 * an invalidation (invalidateOperationalQueries uses refetchType 'none') or a
 * remount within gcTime, which shows the loaded pages with no GET. Every
 * option below is passed explicitly: TanStack spreads options over the client
 * defaults, so an explicit `undefined` would re-enable a refetch (the W5b
 * useWarmingUpRetry focus trap).
 *
 * A page past 0 that answers 409 (gold refreshed since page 0) or 422
 * `lead_view_cursor_invalid` restarts the view at page 0 and says the queue
 * was updated. Any other failed Load next keeps the loaded pages; its Retry
 * is one fetchNextPage. The cursor stays in the cache's page params only.
 */
import { useLayoutEffect, useState } from 'react';
import {
  hashKey,
  keepPreviousData,
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
  type QueryKey,
} from '@tanstack/react-query';
import { ApiError, isWarmingUpError } from '../lib/api';
import { planForReason } from '../lib/retryPlan';
import { queryKeys } from '../lib/queryKeys';
import { LEADS_PAGED_SCOPE, leadsPagedQueryKey, type LeadsRequest } from '../lib/leadsQuery';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import { fetchLeadsPage, type LeadServerOrder, type LeadsPagedPage } from '../lib/apiClients/leadsPaged';
import type { LeadTablePaging, LeadTableSort } from '../components/mortgage/LeadTable.types';

/** Ten pages of 500: page index 9 is the last a cursor is minted for. */
export const LEAD_MAX_PAGES = 10;
export const LEAD_PAGED_GC_MS = 30 * 60_000;
export const LEAD_VIEW_CURSOR_INVALID = 'lead_view_cursor_invalid';
const RETRY_DEFAULTS = { intervalMs: 5_000, maxAttempts: 6 };

/** The rows on screen and the page-0 headers the route reads. */
export interface LeadQueueView {
  leads: LeadsPagedPage['leads'];
  totalMatching: number | null;
  rankedMatching: number | null;
  returnedRows: number | null;
  truncatedAt: number | null;
  dataRefreshedAt: string | null;
  growthAgentVerification: LeadsPagedPage['growthAgentVerification'];
}

interface SelectedView {
  view: LeadQueueView;
  viewId: string | null;
  pagesLoaded: number;
  unavailable: boolean;
}

/** What the table's footer and the decision writes read (LeadTable `paging`). */
export type LeadQueuePaging = LeadTablePaging;

export interface LeadQueuePages {
  data: LeadQueueView | null;
  warmingUp: WarmingUpState | null;
  error: Error | null;
  isFetching: boolean;
  isPlaceholderData: boolean;
  dataUpdatedAt: number | null;
  errorUpdatedAt: number | null;
  /** AsyncState's Retry and the freshness Refresh: page 0 only. */
  manualRetry: () => void;
  paging: LeadQueuePaging;
}

export interface LeadQueuePagesInput {
  request: LeadsRequest;
  order: LeadServerOrder;
  /** Fingerprints of inputs the fetch reads from outside the request (the Growth Agent proof). */
  implicitInputs: readonly string[];
  enabled: boolean;
  keepPrevious: boolean;
}

const RANK_ORDER: LeadServerOrder = { sort: 'rank', dir: 'desc' };
const SERVER_SORTS: ReadonlySet<string> = new Set(['score', 'equity', 'rate', 'confidence']);

/**
 * The server sort for the URL's table sort: a warehouse column sorts the whole
 * view (keyset paged); rank, or a Lakebase-hydrated key (relationship,
 * assignment, outreach) that only the loaded rows can sort, reads rank order.
 */
export function serverOrderOf(sort: LeadTableSort | null): LeadServerOrder {
  return sort && SERVER_SORTS.has(sort.key)
    ? { sort: sort.key as LeadServerOrder['sort'], dir: sort.dir }
    : RANK_ORDER;
}

function selectView(data: InfiniteData<LeadsPagedPage, string | null>): SelectedView {
  const [first] = data.pages;
  return {
    view: {
      leads: data.pages.length === 1 ? first.leads : data.pages.flatMap((page) => page.leads),
      totalMatching: first.totalMatching,
      rankedMatching: first.rankedMatching,
      returnedRows: data.pages.reduce((sum, page) => sum + (page.returnedRows ?? page.leads.length), 0),
      truncatedAt: first.truncatedAt,
      dataRefreshedAt: first.dataRefreshedAt,
      growthAgentVerification: first.growthAgentVerification,
    },
    viewId: first.viewId,
    pagesLoaded: data.pages.length,
    unavailable: first.paging === 'unavailable',
  };
}

/** A later page the server refused because the view itself is stale. */
export function isViewResetError(error: unknown): boolean {
  return error instanceof ApiError
    && (error.status === 409 || (error.status === 422 && error.message === LEAD_VIEW_CURSOR_INVALID));
}

function retryWarmingUp(failureCount: number, error: Error): boolean {
  if (!isWarmingUpError(error)) return false;
  const plan = planForReason(error.reason, error.dependency, RETRY_DEFAULTS);
  return !plan.stop && failureCount < Math.max(0, plan.maxAttempts - 1);
}

function warmingUpDelay(_failureCount: number, error: Error): number {
  return isWarmingUpError(error) ? planForReason(error.reason, error.dependency, RETRY_DEFAULTS).intervalMs : 0;
}

export function useLeadQueuePages({
  request,
  order,
  implicitInputs,
  enabled,
  keepPrevious,
}: LeadQueuePagesInput): LeadQueuePages {
  const queryClient = useQueryClient();
  const queryKey: QueryKey = leadsPagedQueryKey(request, order, implicitInputs);
  const keyHash = hashKey(queryKey);
  const query = useInfiniteQuery<LeadsPagedPage, Error, SelectedView, QueryKey, string | null>({
    queryKey,
    queryFn: ({ pageParam, signal }) => fetchLeadsPage(request, order, pageParam, signal),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    select: selectView,
    enabled,
    staleTime: Infinity,
    gcTime: LEAD_PAGED_GC_MS,
    networkMode: 'always',
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    meta: { auditedPages: true },
    ...(keepPrevious && { placeholderData: keepPreviousData }),
    retry: retryWarmingUp,
    retryDelay: warmingUpDelay,
  });
  // The rows a Refresh (or a view reset) re-reads stay on screen, marked as
  // updating, until page 0 answers: never a skeleton flash.
  const [held, setHeld] = useState<{ keyHash: string; selected: SelectedView } | null>(null);
  const [queueUpdated, setQueueUpdated] = useState<string | null>(null);

  // One paged view at a time (see the module doc).
  useLayoutEffect(() => {
    queryClient.removeQueries({
      queryKey: queryKeys.leads([LEADS_PAGED_SCOPE]),
      predicate: (cached) => cached.queryHash !== keyHash,
    });
  }, [queryClient, keyHash]);

  const restart = (stale: boolean) => {
    setHeld(query.data ? { keyHash, selected: query.data } : null);
    setQueueUpdated(stale ? keyHash : null);
    void queryClient.resetQueries({ queryKey, exact: true });
  };
  const loadNext = () => {
    if (!query.hasNextPage || query.isFetchingNextPage) return;
    void query.fetchNextPage().then((result) => {
      if (result.isFetchNextPageError && isViewResetError(result.error)) restart(true);
      else if (!result.isFetchNextPageError) setQueueUpdated(null);
    });
  };

  const heldNow = held !== null && held.keyHash === keyHash && query.data === undefined && query.isFetching
    ? held.selected
    : null;
  const selected = query.data ?? heldNow;
  const nextFailed = query.isFetchNextPageError;
  const failure = query.failureReason;
  let warmingUp: WarmingUpState | null = null;
  if ((query.data === undefined || query.isPlaceholderData) && isWarmingUpError(failure)) {
    const plan = planForReason(failure.reason, failure.dependency, RETRY_DEFAULTS);
    if (!plan.stop) {
      warmingUp = {
        dependency: failure.dependency,
        label: plan.label,
        attempt: Math.min(query.failureCount + 1, Math.max(1, plan.maxAttempts)),
        maxAttempts: plan.maxAttempts,
        correlationId: failure.correlationId,
        intervalMs: plan.intervalMs,
      };
    }
  }

  return {
    data: selected?.view ?? null,
    warmingUp,
    // A failed Load next is the footer's, not the panel's.
    error: nextFailed ? null : query.error,
    isFetching: query.isFetching && !query.isFetchingNextPage,
    isPlaceholderData: query.isPlaceholderData || heldNow !== null,
    dataUpdatedAt: query.dataUpdatedAt || null,
    errorUpdatedAt: query.errorUpdatedAt || null,
    manualRetry: () => restart(false),
    paging: {
      viewId: query.data?.viewId ?? null,
      pagesLoaded: selected?.pagesLoaded ?? 0,
      hasMore: query.hasNextPage && !query.isPlaceholderData,
      unavailable: selected?.unavailable ?? false,
      capped: (selected?.pagesLoaded ?? 0) >= LEAD_MAX_PAGES,
      fetchingNext: query.isFetchingNextPage,
      nextError: nextFailed && !isViewResetError(query.error),
      queueUpdated: queueUpdated === keyHash,
      loadNext,
      retryNext: loadNext,
    },
  };
}
