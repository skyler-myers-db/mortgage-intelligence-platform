/**
 * @vitest-environment happy-dom
 *
 * useBorrowerSearch is the ONE borrower search behind the topbar and the ⌘K
 * palette (2026-09-21 audit runtime-06, shell-07 item 1): one debounce, one
 * cache entry per term, a consumed abort signal, nothing persisted.
 */
import { act, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import { isPersistableQueryKey } from '../lib/queryPersist';
import { mount, type Mounted } from '../test/render';
import type { LeadSummary } from '../types';

const apiMocks = vi.hoisted(() => ({ borrowerSearch: vi.fn() }));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { borrowerSearch: apiMocks.borrowerSearch },
}));

import {
  BORROWER_SEARCH_DEBOUNCE_MS,
  MIN_BORROWER_SEARCH_CHARS,
  useBorrowerSearch,
  type BorrowerSearch,
} from './useBorrowerSearch';

const ROWS = [
  { borrower_id: 'B-0000000000001', city: 'Chicago', state: 'IL', zip: '60611' },
] as unknown as LeadSummary[];

let latest: BorrowerSearch | null = null;

function Probe({ query, enabled }: { query: string; enabled?: boolean }) {
  const search = useBorrowerSearch(query, { enabled });
  useEffect(() => {
    latest = search;
  });
  return null;
}

let queryClient: QueryClient;
let mounted: Mounted;

async function render(query: string, enabled = true): Promise<void> {
  const ui = (
    <QueryClientProvider client={queryClient}>
      <Probe query={query} enabled={enabled} />
    </QueryClientProvider>
  );
  if (mounted) await mounted.rerender(ui);
  else mounted = await mount(ui);
}

async function wait(ms: number): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

/** Past the debounce, then the answer's render. */
async function settle(): Promise<void> {
  await wait(BORROWER_SEARCH_DEBOUNCE_MS + 40);
  await wait(20);
  await wait(20);
}

beforeEach(() => {
  queryClient = new QueryClient();
  apiMocks.borrowerSearch.mockReset();
  apiMocks.borrowerSearch.mockResolvedValue(ROWS);
  latest = null;
});

afterEach(() => {
  mounted?.unmount();
  mounted = undefined as unknown as Mounted;
});

describe('useBorrowerSearch', () => {
  it('requests nothing under two characters and reads idle', async () => {
    expect(MIN_BORROWER_SEARCH_CHARS).toBe(2);
    await render(' C ');
    await settle();
    expect(apiMocks.borrowerSearch).not.toHaveBeenCalled();
    expect(latest).toEqual(expect.objectContaining({ term: '', rows: [], status: 'idle' }));
  });

  it('makes one request per debounce, keyed on the trimmed term', async () => {
    expect(BORROWER_SEARCH_DEBOUNCE_MS).toBe(160);
    await render('Ch');
    await render('Chi');
    await render(' Chic ');
    expect(latest?.status).toBe('loading');
    expect(apiMocks.borrowerSearch).not.toHaveBeenCalled();
    await settle();
    expect(apiMocks.borrowerSearch).toHaveBeenCalledTimes(1);
    expect(apiMocks.borrowerSearch.mock.calls[0][0]).toBe('Chic');
    expect(latest).toEqual(expect.objectContaining({ term: 'Chic', dataTerm: 'Chic', rows: ROWS, status: 'idle' }));
    expect(queryClient.getQueryData(queryKeys.borrowerSearch('Chic'))).toEqual(ROWS);
  });

  it('answers a term already in the cache without a request (the other surface searched it)', async () => {
    queryClient.setQueryData(queryKeys.borrowerSearch('Chicago'), ROWS);
    await render('Chicago');
    await settle();
    expect(apiMocks.borrowerSearch).not.toHaveBeenCalled();
    expect(latest?.rows).toEqual(ROWS);
  });

  it('consumes the abort signal: a superseded term aborts its request', async () => {
    let firstSignal: AbortSignal | null = null;
    apiMocks.borrowerSearch.mockImplementationOnce((_term: string, signal: AbortSignal) => {
      firstSignal = signal;
      return new Promise<LeadSummary[]>(() => undefined);
    });
    await render('Chic');
    await settle();
    expect(firstSignal).toBeInstanceOf(AbortSignal);
    expect((firstSignal as AbortSignal | null)?.aborted).toBe(false);
    await render('Tacoma');
    await settle();
    expect((firstSignal as AbortSignal | null)?.aborted).toBe(true);
    expect(apiMocks.borrowerSearch.mock.calls.map((call: unknown[]) => call[0])).toEqual(['Chic', 'Tacoma']);
  });

  it('reads empty and error for the settled term', async () => {
    apiMocks.borrowerSearch.mockResolvedValueOnce([]);
    await render('Nowhere');
    await settle();
    expect(latest).toEqual(expect.objectContaining({ status: 'empty', dataTerm: 'Nowhere', rows: [] }));
    apiMocks.borrowerSearch.mockRejectedValueOnce(new Error('warehouse down'));
    await render('Elsewhere');
    await settle();
    expect(latest).toEqual(expect.objectContaining({ status: 'error', rows: [] }));
  });

  it('a disabled search requests nothing and keeps its last answer readable', async () => {
    await render('Chic');
    await settle();
    await render('Chic', false);
    expect(latest).toEqual(expect.objectContaining({ rows: ROWS, status: 'idle' }));
    await render('Chicago', false);
    await settle();
    expect(apiMocks.borrowerSearch).toHaveBeenCalledTimes(1);
  });

  it('searchNow skips the debounce on the same key and shares one request', async () => {
    await render('Chic');
    let answer: LeadSummary[] = [];
    await act(async () => {
      answer = await (latest as BorrowerSearch).searchNow('Chic');
    });
    await settle();
    expect(answer).toEqual(ROWS);
    expect(apiMocks.borrowerSearch).toHaveBeenCalledTimes(1);
  });

  it('never persists a search (outside the allow-list and the borrower prefix)', () => {
    const key = queryKeys.borrowerSearch('B-0000000000001');
    expect(key).toEqual(['mip', 'borrower-search', 'B-0000000000001']);
    expect(isPersistableQueryKey(key)).toBe(false);
    expect(isPersistableQueryKey(queryKeys.paletteRecentEvents())).toBe(false);
    expect(isPersistableQueryKey(queryKeys.paletteAssets())).toBe(false);
    expect(key[1]).not.toBe('borrower');
  });
});
