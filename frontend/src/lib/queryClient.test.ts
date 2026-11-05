import { afterEach, describe, expect, it, vi } from 'vitest';
import { onlineManager, QueryClient, QueryObserver, type Query } from '@tanstack/react-query';
import {
  createMipQueryClient,
  DEFAULT_QUERY_GC_MS,
  DEFAULT_QUERY_STALE_MS,
  refetchOnReconnectIfNeverServed,
} from './queryClient';
import { invalidateOperationalQueries, queryKeys } from './queryKeys';
import { ApiError } from './api';
import { LEADS_PAGED_SCOPE, leadsPagedQueryKey } from './leadsQuery';

describe('createMipQueryClient', () => {
  it('uses bounded freshness defaults and disables automatic focus refetch', () => {
    const client = createMipQueryClient();
    const queries = client.getDefaultOptions().queries;

    expect(queries?.staleTime).toBe(DEFAULT_QUERY_STALE_MS);
    expect(queries?.gcTime).toBe(DEFAULT_QUERY_GC_MS);
    expect(queries?.refetchOnWindowFocus).toBe(false);
    expect(queries?.refetchOnReconnect).toBe(refetchOnReconnectIfNeverServed);
    expect(client.getDefaultOptions().mutations?.retry).toBe(false);
  });

  it('never re-reads the Lead Queue paged view on its own, and nothing else changes (W5c)', () => {
    const client = createMipQueryClient();
    const paged = { refetchOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false, staleTime: Infinity };
    // The literal in queryClient.ts is the scope leadsQuery exports.
    expect(client.getQueryDefaults(leadsPagedQueryKey({}, { sort: 'rank', dir: 'desc' }))).toEqual(paged);
    expect(client.getQueryDefaults(queryKeys.leads([LEADS_PAGED_SCOPE]))).toEqual(paged);
    // The plain leads reads (Segment Intelligence, Top leads) keep the app defaults.
    expect(client.getQueryDefaults(queryKeys.leads(['segment-intelligence', {}]))).toEqual({});
    expect(client.getQueryDefaults(queryKeys.leads(['top-quick-pick', '5']))).toEqual({});
  });

  it('retries warming-up and breaker-open dependency states, but not exhausted retries', () => {
    const client = createMipQueryClient();
    const retry = client.getDefaultOptions().queries?.retry;
    expect(typeof retry).toBe('function');

    const warming = new ApiError('warming', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'warming_up',
    });
    const breaker = new ApiError('cooling', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'breaker_open',
    });
    const exhausted = new ApiError('exhausted', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'retries_exhausted',
    });
    const rateLimited = new ApiError('rate limited', {
      path: '/api/borrowers/B-TEST',
      status: 429,
      retryable: true,
      dependency: 'warehouse',
      reason: 'rate_limited',
    });

    expect((retry as (count: number, error: Error) => boolean)(0, warming)).toBe(true);
    expect((retry as (count: number, error: Error) => boolean)(5, warming)).toBe(false);
    expect((retry as (count: number, error: Error) => boolean)(0, breaker)).toBe(true);
    expect((retry as (count: number, error: Error) => boolean)(1, breaker)).toBe(false);
    expect((retry as (count: number, error: Error) => boolean)(0, exhausted)).toBe(false);
    expect((retry as (count: number, error: Error) => boolean)(0, rateLimited)).toBe(false);
  });

  it('never retries a 409 actor_changed: nothing ran, and the health recheck resets the tab (a4)', () => {
    const retry = createMipQueryClient().getDefaultOptions().queries?.retry as (count: number, error: Error) => boolean;
    // retryable: true would not change it; the binding refusal is never a warming state.
    const refused = new ApiError('The signed-in user changed in this browser. Nothing was recorded.', {
      path: '/api/v1/leads',
      status: 409,
      retryable: true,
      reason: 'actor_changed',
    });

    expect(retry(0, refused)).toBe(false);
  });
});

function failure(reason: string | undefined, extra: { status?: number | null; aborted?: boolean } = {}): ApiError {
  return new ApiError('failed', { path: '/api/v1/leads', status: extra.status ?? null, retryable: false, reason, aborted: extra.aborted });
}

function queryIn(error: unknown, meta?: Record<string, unknown>): Query {
  return { state: { error }, meta } as unknown as Query;
}

describe('refetchOnReconnectIfNeverServed', () => {
  it('refetches on reconnect only a read whose last attempt never reached the server', () => {
    expect(refetchOnReconnectIfNeverServed(queryIn(null))).toBe(false);
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('offline')))).toBe(true);
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('unreachable')))).toBe(true);
    // The server answered: a warming 503, an ended session, an unreadable body.
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('warming_up', { status: 503 })))).toBe(false);
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('session_expired', { status: 401 })))).toBe(false);
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('unreadable_response', { status: 200 })))).toBe(false);
    // The caller aborted it, or every served page writes a row.
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('unreachable', { aborted: true })))).toBe(false);
    expect(refetchOnReconnectIfNeverServed(queryIn(failure('unreachable'), { auditedPages: true }))).toBe(false);
  });
});

describe('createMipQueryClient on reconnect', () => {
  afterEach(() => {
    onlineManager.setOnline(true);
  });

  it('never re-reads a served stale query, and completes one that never reached the server', async () => {
    const client = createMipQueryClient();
    client.mount();
    onlineManager.setOnline(true);
    const served = vi.fn(async () => 'rows');
    let reachable = false;
    const neverReached = vi.fn(async () => {
      if (!reachable) {
        reachable = true;
        throw failure('unreachable');
      }
      return 'rows';
    });
    // staleTime 0: both are stale at once, as every read is once its window has passed.
    const servedObserver = new QueryObserver(client, { queryKey: ['mip', 'leads', 'served'], queryFn: served, staleTime: 0 });
    const failedObserver = new QueryObserver(client, { queryKey: ['mip', 'borrower', 'failed'], queryFn: neverReached, staleTime: 0 });
    const unsubscribe = [servedObserver.subscribe(() => undefined), failedObserver.subscribe(() => undefined)];
    try {
      await vi.waitFor(() => expect(servedObserver.getCurrentResult().status).toBe('success'));
      await vi.waitFor(() => expect(failedObserver.getCurrentResult().status).toBe('error'));

      onlineManager.setOnline(false);
      onlineManager.setOnline(true);

      await vi.waitFor(() => expect(failedObserver.getCurrentResult().status).toBe('success'));
      expect(neverReached).toHaveBeenCalledTimes(2);
      expect(served).toHaveBeenCalledTimes(1);
    } finally {
      unsubscribe.forEach((stop) => stop());
      client.unmount();
      client.clear();
    }
  });
});

describe('invalidateOperationalQueries', () => {
  it('invalidates only operational data families, not static config or Genie answers', async () => {
    const client = new QueryClient();
    const invalidate = vi
      .spyOn(client, 'invalidateQueries')
      .mockImplementation(() => Promise.resolve());

    await invalidateOperationalQueries(client);

    expect(invalidate.mock.calls.map(([arg]) => arg)).toEqual([
      { queryKey: ['mip', 'leads'], refetchType: 'none' },
      { queryKey: ['mip', 'borrower'], refetchType: 'none' },
      { queryKey: ['mip', 'sales'], refetchType: 'none' },
      { queryKey: ['mip', 'audit'], refetchType: 'none' },
      { queryKey: ['mip', 'portfolio'], refetchType: 'none' },
      { queryKey: ['mip', 'segments'], refetchType: 'none' },
      { queryKey: ['mip', 'analytics'], refetchType: 'none' },
      { queryKey: ['mip', 'activation'], refetchType: 'none' },
    ]);

    const invalidated = invalidate.mock.calls
      .map(([arg]) => arg?.queryKey)
      .filter((key): key is NonNullable<typeof key> => key !== undefined);
    expect(invalidated).not.toContainEqual(queryKeys.footprint());
    expect(invalidated).not.toContainEqual(queryKeys.configOptions());
    expect(invalidated).not.toContainEqual(queryKeys.campaigns());
    expect(invalidated).not.toContainEqual(queryKeys.workspace());
    expect(invalidated).not.toContainEqual(queryKeys.genieStart());
  });
});
