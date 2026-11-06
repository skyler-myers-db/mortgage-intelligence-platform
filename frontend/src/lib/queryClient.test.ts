import { afterEach, describe, expect, it, vi } from 'vitest';
import { onlineManager, QueryClient, QueryObserver, type Query } from '@tanstack/react-query';
import {
  createMipQueryClient,
  DEFAULT_QUERY_GC_MS,
  DEFAULT_QUERY_STALE_MS,
  refetchOnReconnectAfterNetworkLoss,
} from './queryClient';
import { invalidateOperationalQueries, queryKeys } from './queryKeys';
import { ApiError } from './api';
import { getJson } from './apiTransport';
import { LEADS_PAGED_SCOPE, leadsPagedQueryKey } from './leadsQuery';

describe('createMipQueryClient', () => {
  it('uses bounded freshness defaults and disables automatic focus refetch', () => {
    const client = createMipQueryClient();
    const queries = client.getDefaultOptions().queries;

    expect(queries?.staleTime).toBe(DEFAULT_QUERY_STALE_MS);
    expect(queries?.gcTime).toBe(DEFAULT_QUERY_GC_MS);
    expect(queries?.refetchOnWindowFocus).toBe(false);
    expect(queries?.refetchOnReconnect).toBe(refetchOnReconnectAfterNetworkLoss);
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

function failure(
  reason: string | undefined,
  extra: { status?: number | null; aborted?: boolean; path?: string } = {},
): ApiError {
  return new ApiError('failed', {
    path: extra.path ?? '/api/v1/leads',
    status: extra.status ?? null,
    retryable: false,
    reason,
    aborted: extra.aborted,
  });
}

/** A reviewed audit-free read (lib/auditFreeReads), as the transport records its path. */
const AUDIT_FREE = '/api/v1/analytics/executive?states=CA';

function queryIn(error: unknown, meta?: Record<string, unknown>): Query {
  return { state: { error }, meta } as unknown as Query;
}

describe('refetchOnReconnectAfterNetworkLoss', () => {
  it('refetches on reconnect only a read whose last attempt never reached the server', () => {
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(null))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline')))).toBe(true);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreachable')))).toBe(true);
    // The server answered: a warming 503, an ended session, an unreadable body.
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('warming_up', { status: 503 })))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('session_expired', { status: 401 })))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreadable_response', { status: 200 })))).toBe(false);
    // An audited read answered 200 and the body was lost on the way: offline
    // or unreachable, with a status. Its row is written; a re-read adds one.
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreachable', { status: 200 })))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline', { status: 200 })))).toBe(false);
    const proof = '/api/v1/borrowers/B-P5YP9ESW32R7Z/proof';
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline', { status: 200, path: proof })))).toBe(false);
    // The caller aborted it, or every served page writes a row.
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreachable', { aborted: true })))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreachable'), { auditedPages: true }))).toBe(false);
  });

  it('completes an audit-free read whose answer the network lost, and nothing else on that path', () => {
    // It writes no row, so the lost 200 is completed like a read that never got a status.
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreachable', { status: 200, path: AUDIT_FREE })))).toBe(true);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline', { status: 200, path: AUDIT_FREE })))).toBe(true);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline', { path: AUDIT_FREE })))).toBe(true);
    // A path nobody reviewed counts as audited (fail-closed).
    const unreviewed = '/api/v1/analytics/never-reviewed';
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreachable', { status: 200, path: unreviewed })))).toBe(false);
    // The server answered with no network loss: still never re-read on reconnect.
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('unreadable_response', { status: 200, path: AUDIT_FREE })))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('warming_up', { status: 503, path: AUDIT_FREE })))).toBe(false);
    expect(refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline', { status: 200, path: AUDIT_FREE, aborted: true })))).toBe(false);
    expect(
      refetchOnReconnectAfterNetworkLoss(queryIn(failure('offline', { status: 200, path: AUDIT_FREE }), { auditedPages: true })),
    ).toBe(false);
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

  it('never re-reads a read the server answered 200 whose body was lost mid-transfer', async () => {
    // Through the real transport: the second response's body stream breaks
    // after its 200, as when the network drops mid-transfer. The server
    // served that read, so an audited one has written its row already.
    const encoder = new TextEncoder();
    const responses = [
      () => new Response(JSON.stringify({ rows: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('{"rows": ['));
              controller.error(new TypeError('network connection was lost'));
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      () => new Response(JSON.stringify({ rows: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ];
    const fetchMock = vi.fn(async () => {
      const next = responses.shift();
      if (!next) throw new Error('unexpected extra request');
      return next();
    });
    vi.stubGlobal('fetch', fetchMock);
    const client = createMipQueryClient();
    client.mount();
    onlineManager.setOnline(true);
    const observer = new QueryObserver(client, {
      queryKey: ['mip', 'leads', 'body-lost'],
      queryFn: ({ signal }) => getJson<{ rows: unknown[] }>('/leads', signal),
      staleTime: 0,
    });
    // The control: a read that got no status, which the same reconnect completes.
    let reachable = false;
    const neverReached = vi.fn(async () => {
      if (!reachable) {
        reachable = true;
        throw failure('unreachable');
      }
      return 'rows';
    });
    const control = new QueryObserver(client, { queryKey: ['mip', 'borrower', 'no-status'], queryFn: neverReached, staleTime: 0 });
    const unsubscribe = [observer.subscribe(() => undefined), control.subscribe(() => undefined)];
    try {
      await vi.waitFor(() => expect(observer.getCurrentResult().status).toBe('success'));
      await vi.waitFor(() => expect(control.getCurrentResult().status).toBe('error'));
      // An explicit refetch (a Refresh): the server answers 200, the body is lost.
      await observer.refetch();
      const error = observer.getCurrentResult().error;
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).reason).toBe('unreachable');
      expect((error as ApiError).status).toBe(200);
      expect(fetchMock).toHaveBeenCalledTimes(2);

      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
      // One reconnect pass decides every query: once the control has re-read,
      // a re-read of the answered one would already have called fetch.
      await vi.waitFor(() => expect(control.getCurrentResult().status).toBe('success'));
      expect(neverReached).toHaveBeenCalledTimes(2);

      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(observer.getCurrentResult().status).toBe('error');
    } finally {
      unsubscribe.forEach((stop) => stop());
      client.unmount();
      client.clear();
      vi.unstubAllGlobals();
    }
  });

  it('completes an audit-free read whose 200 body was lost, beside an audited one it never re-reads', async () => {
    // Through the real transport, both answered 200 and lost their body. The
    // analytics read writes no row (lib/auditFreeReads), so the reconnect
    // completes it and the panel loads by itself; /leads wrote VIEW_LEADS.
    const encoder = new TextEncoder();
    const ok = () => new Response(JSON.stringify({ rows: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    const bodyLost = () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode('{"rows": ['));
            controller.error(new TypeError('network connection was lost'));
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    const scripted: Record<string, Array<() => Response>> = {
      '/api/v1/leads': [ok, bodyLost],
      '/api/v1/analytics/executive': [ok, bodyLost, ok],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : input.href, 'http://app.test');
      const next = scripted[url.pathname]?.shift();
      if (!next) throw new Error(`unexpected request ${url.pathname}`);
      return next();
    });
    const calls = (pathname: string) =>
      fetchMock.mock.calls.filter(([input]) => new URL(String(input), 'http://app.test').pathname === pathname).length;
    vi.stubGlobal('fetch', fetchMock);
    const client = createMipQueryClient();
    client.mount();
    onlineManager.setOnline(true);
    const audited = new QueryObserver(client, {
      queryKey: ['mip', 'leads', 'body-lost-audited'],
      queryFn: ({ signal }) => getJson<{ rows: unknown[] }>('/leads', signal),
      staleTime: 0,
    });
    const auditFree = new QueryObserver(client, {
      queryKey: ['mip', 'analytics', 'body-lost-audit-free'],
      queryFn: ({ signal }) => getJson<{ rows: unknown[] }>('/analytics/executive', signal),
      staleTime: 0,
    });
    const unsubscribe = [audited.subscribe(() => undefined), auditFree.subscribe(() => undefined)];
    try {
      await vi.waitFor(() => expect(audited.getCurrentResult().status).toBe('success'));
      await vi.waitFor(() => expect(auditFree.getCurrentResult().status).toBe('success'));
      // A Refresh of each: the server answers 200 and the body is lost.
      await Promise.all([audited.refetch(), auditFree.refetch()]);
      for (const observer of [audited, auditFree]) {
        const error = observer.getCurrentResult().error;
        expect(error).toBeInstanceOf(ApiError);
        expect((error as ApiError).reason).toBe('unreachable');
        expect((error as ApiError).status).toBe(200);
      }

      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
      // One reconnect pass decides both: once the audit-free read has
      // re-read, a re-read of the audited one would already have called fetch.
      await vi.waitFor(() => expect(auditFree.getCurrentResult().status).toBe('success'));
      expect(calls('/api/v1/analytics/executive')).toBe(3);

      expect(calls('/api/v1/leads')).toBe(2);
      expect(audited.getCurrentResult().status).toBe('error');
    } finally {
      unsubscribe.forEach((stop) => stop());
      client.unmount();
      client.clear();
      vi.unstubAllGlobals();
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
