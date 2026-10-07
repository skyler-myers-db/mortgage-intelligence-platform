/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue's paged view (W5c, D-audit-reads-a): every served page of
 * /api/leads writes one VIEW_LEADS audit row, so the ONLY reads are the
 * reader's. Pinned on the real hook over the app's real QueryClient (its
 * defaults and setQueryDefaults included), with the page fetch counted:
 *
 *   - a first mount: one page 0; a remount, a window focus, a reconnect, an
 *     operational invalidation, a health recovery and an unreachable-probe
 *     recovery: ZERO more;
 *   - Load next: exactly one GET per click (a double click is still one);
 *   - Refresh after two pages: one GET, page 0 only;
 *   - a later page answering 409 or 422 `lead_view_cursor_invalid`: the view
 *     restarts at page 0 and says the queue was updated;
 *   - any other failed Load next keeps the loaded pages; Retry is one GET;
 *   - a sort change reads page 0 of the new order and drops the old view;
 *   - no query key ever holds a cursor.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { focusManager, onlineManager, QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import { createMipQueryClient } from '../lib/queryClient';
import { invalidateOperationalQueries } from '../lib/queryKeys';
import { refetchUnreachableQueries } from '../components/connectionState';
import { refetchRecoveredQueries } from '../components/healthRecovery';
import type { LeadServerOrder, LeadsPagedPage } from '../lib/apiClients/leadsPaged';
import type { LeadsRequest } from '../lib/leadsQuery';
import type { LeadSummary } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fetchMock = vi.hoisted(() => ({
  calls: [] as Array<{ cursor: string | null; order: { sort: string; dir: string } }>,
  next: [] as Array<() => Promise<unknown>>,
}));

vi.mock('../lib/apiClients/leadsPaged', () => ({
  fetchLeadsPage: (_request: unknown, order: { sort: string; dir: string }, cursor: string | null) => {
    fetchMock.calls.push({ cursor, order });
    const answer = fetchMock.next.shift();
    if (!answer) throw new Error('unexpected /api/leads read');
    return answer();
  },
}));

const { useLeadQueuePages } = await import('./lead-queue.pages');
type Pages = ReturnType<typeof useLeadQueuePages>;

const VIEW = '0123456789abcdef0123456789abcdef';

function rows(page: number, count: number): LeadSummary[] {
  return Array.from({ length: count }, (_, i) => ({ borrower_id: `B-P${page}R${String(i).padStart(10, '0')}` }) as LeadSummary);
}

function page(index: number, { next = `cur${index + 1}.sig`, count = 500, view = VIEW } = {}): LeadsPagedPage {
  return {
    leads: rows(index, count),
    totalMatching: 1_284,
    rankedMatching: 1_284,
    returnedRows: count,
    truncatedAt: 500,
    dataRefreshedAt: '2026-10-01T00:00:00Z',
    growthAgentVerification: null,
    viewId: view,
    pageIndex: index,
    nextCursor: next,
    paging: 'ok',
  };
}

const answer = (value: LeadsPagedPage) => () => Promise.resolve(value);
const fail = (error: Error) => () => Promise.reject(error);
const RANK: LeadServerOrder = { sort: 'rank', dir: 'desc' };
const REQUEST: LeadsRequest = { segment: undefined, geo: { states: ['IL'] }, opts: { growthAgentProof: null } };

describe('useLeadQueuePages', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let latest: Pages | null = null;

  function Probe({ order = RANK }: { order?: LeadServerOrder }) {
    latest = useLeadQueuePages({ request: REQUEST, order, implicitInputs: ['no-proof'], enabled: true, keepPrevious: true });
    return null;
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  async function mount(order: LeadServerOrder = RANK): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe order={order} />
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  async function unmount(): Promise<void> {
    await act(async () => {
      root.render(<QueryClientProvider client={client}>{null}</QueryClientProvider>);
    });
  }

  const pages = (): Pages => {
    if (!latest) throw new Error('not rendered');
    return latest;
  };

  beforeEach(() => {
    fetchMock.calls = [];
    fetchMock.next = [];
    latest = null;
    client = createMipQueryClient();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    client.clear();
    focusManager.setFocused(undefined);
    onlineManager.setOnline(true);
  });

  it('reads page 0 once; a remount, focus, reconnect or invalidation reads nothing', async () => {
    fetchMock.next.push(answer(page(0)));
    await mount();
    expect(fetchMock.calls).toEqual([{ cursor: null, order: RANK }]);
    expect(pages().data?.leads).toHaveLength(500);

    await unmount();
    await mount();
    expect(pages().data?.leads, 'the cached pages, with no GET').toHaveLength(500);

    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await act(async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    await act(async () => {
      await invalidateOperationalQueries(client);
    });
    await settle();
    expect(fetchMock.calls).toHaveLength(1);
    expect(pages().data?.leads).toHaveLength(500);
  });

  it('a health or unreachable recovery never re-reads a failed view', async () => {
    fetchMock.next.push(fail(new ApiError('unreachable', { path: '/api/leads', reason: 'unreachable' })));
    await mount();
    expect(pages().error).not.toBeNull();
    await act(async () => refetchUnreachableQueries(client));
    await settle();
    expect(fetchMock.calls).toHaveLength(1);

    client.clear();
    await unmount();
    fetchMock.next.push(fail(new ApiError('down', {
      path: '/api/leads', status: 503, retryable: true, dependency: 'sql_warehouse', reason: 'retries_exhausted',
    })));
    await mount();
    expect(fetchMock.calls).toHaveLength(2);
    expect(pages().error).not.toBeNull();
    await act(async () => refetchRecoveredQueries(client, ['sql_warehouse']));
    await settle();
    expect(fetchMock.calls, 'the recovery reads nothing; the reader retries').toHaveLength(2);
  });

  it('Load next reads exactly one page per click; Refresh reads page 0 only', async () => {
    fetchMock.next.push(answer(page(0)));
    await mount();

    let release: () => void = () => undefined;
    fetchMock.next.push(() => new Promise((resolve) => {
      release = () => resolve(page(1));
    }));
    await act(async () => {
      pages().paging.loadNext();
    });
    expect(pages().paging.fetchingNext).toBe(true);
    await act(async () => {
      pages().paging.loadNext();
    });
    await act(async () => release());
    await settle();
    expect(fetchMock.calls.map((call) => call.cursor)).toEqual([null, 'cur1.sig']);
    expect(pages().data?.leads).toHaveLength(1_000);
    expect(pages().paging.pagesLoaded).toBe(2);
    expect(pages().paging.viewId).toBe(VIEW);

    fetchMock.next.push(answer(page(0, { view: 'fedcba9876543210fedcba9876543210' })));
    await act(async () => pages().manualRetry());
    await settle();
    expect(fetchMock.calls.map((call) => call.cursor)).toEqual([null, 'cur1.sig', null]);
    expect(pages().paging.pagesLoaded).toBe(1);
    expect(pages().data?.leads).toHaveLength(500);
    expect(pages().paging.queueUpdated).toBe(false);
  });

  it.each([
    ['409 (gold refreshed since page 0)', new ApiError('stale', { path: '/api/leads', status: 409 })],
    ['422 lead_view_cursor_invalid', new ApiError('lead_view_cursor_invalid', { path: '/api/leads', status: 422 })],
  ])('a later page answering %s restarts the view at page 0 and says so', async (_label, error) => {
    fetchMock.next.push(answer(page(0)));
    await mount();
    fetchMock.next.push(fail(error));
    fetchMock.next.push(answer(page(0, { view: 'fedcba9876543210fedcba9876543210' })));
    await act(async () => pages().paging.loadNext());
    await settle();
    await settle();
    expect(fetchMock.calls.map((call) => call.cursor)).toEqual([null, 'cur1.sig', null]);
    expect(pages().paging.queueUpdated).toBe(true);
    expect(pages().paging.pagesLoaded).toBe(1);
    expect(pages().paging.nextError).toBe(false);
    expect(pages().error).toBeNull();
  });

  it('any other failed Load next keeps the loaded pages; Retry is one GET of the same page', async () => {
    fetchMock.next.push(answer(page(0)));
    await mount();
    fetchMock.next.push(fail(new ApiError('boom', { path: '/api/leads', status: 500 })));
    await act(async () => pages().paging.loadNext());
    await settle();
    expect(pages().paging.nextError).toBe(true);
    expect(pages().error, 'the footer owns the failure, not the panel').toBeNull();
    expect(pages().data?.leads).toHaveLength(500);

    fetchMock.next.push(answer(page(1)));
    await act(async () => pages().paging.retryNext());
    await settle();
    expect(fetchMock.calls.map((call) => call.cursor)).toEqual([null, 'cur1.sig', 'cur1.sig']);
    expect(pages().data?.leads).toHaveLength(1_000);
    expect(pages().paging.nextError).toBe(false);
  });

  it('a sort change reads page 0 of the new order and drops the old view; no key holds a cursor', async () => {
    fetchMock.next.push(answer(page(0)));
    await mount();
    fetchMock.next.push(answer(page(1)));
    await act(async () => pages().paging.loadNext());
    await settle();

    const equity: LeadServerOrder = { sort: 'equity', dir: 'desc' };
    fetchMock.next.push(answer(page(0, { view: 'fedcba9876543210fedcba9876543210' })));
    await mount(equity);
    expect(fetchMock.calls.slice(2)).toEqual([{ cursor: null, order: equity }]);
    expect(pages().paging.pagesLoaded).toBe(1);

    const keys = client.getQueryCache().getAll().map((query) => JSON.stringify(query.queryKey));
    expect(keys.filter((key) => key.includes('lead-queue-paged'))).toHaveLength(1);
    expect(keys.join('\n')).not.toContain('cur');
    expect(keys.join('\n')).not.toContain('.sig');
  });

  it('marks the view audited so every recovery predicate can skip it', async () => {
    fetchMock.next.push(answer(page(0)));
    await mount();
    const [query] = client.getQueryCache().findAll({ queryKey: ['mip', 'leads', 'lead-queue-paged'] });
    expect(query.meta).toEqual({ auditedPages: true });
    expect((query.options as { staleTime?: unknown }).staleTime).toBe(Infinity);
  });
});
