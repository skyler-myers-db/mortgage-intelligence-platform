// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads source text under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { onlineManager, QueryClient, QueryObserver, type Query } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, getJson } from '../lib/apiTransport';
import { createMipQueryClient } from '../lib/queryClient';
import {
  answeredAuditFreeLoss,
  hasAuditedPages,
  isOneAuditFreeRead,
  neverAnswered,
  optedOutOfReconnect,
  refetchUnreachableQueries,
} from './connectionState';

declare const process: { cwd(): string };

/**
 * The ONE audited-pages exclusion (W5c, D-audit-reads-a; integrator C16): a
 * query whose every served page writes an audit row (`meta.auditedPages`) is
 * never re-read by a recovery. healthRecovery.refetchRecoveredQueries uses
 * the same predicate (pinned in healthRecovery.test.tsx).
 */
describe('hasAuditedPages', () => {
  it('is true only for meta.auditedPages === true', () => {
    expect(hasAuditedPages({ meta: { auditedPages: true } })).toBe(true);
    expect(hasAuditedPages({ meta: { auditedPages: 'yes' } })).toBe(false);
    expect(hasAuditedPages({ meta: {} })).toBe(false);
    expect(hasAuditedPages({ meta: undefined })).toBe(false);
  });
});

describe('refetchUnreachableQueries', () => {
  const cleanups: Array<() => void> = [];
  afterEach(() => {
    for (const cleanup of cleanups.splice(0)) cleanup();
  });

  it('re-reads an unreachable active query, never an audited paged one or an audited one the server answered', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const unreachable = () => Promise.reject(new ApiError('unreachable', { path: '/api/x', reason: 'unreachable' }));
    // A 200 whose body was lost mid-transfer (apiTransport _readJson): served.
    // /api/v1/leads writes VIEW_LEADS, and an unreviewed path counts as audited.
    const bodyLost = (path: string) => () =>
      Promise.reject(new ApiError('unreachable', { path, method: 'GET', reason: 'unreachable', status: 200 }));
    const plain = vi.fn(unreachable);
    const audited = vi.fn(unreachable);
    const answered = vi.fn(bodyLost('/api/v1/leads?limit=5'));
    const unreviewed = vi.fn(bodyLost('/api/x'));
    // The same lost 200 on a reviewed audit-free read: it wrote no row.
    const auditFree = vi.fn(bodyLost('/api/v1/analytics/executive'));
    // Observed (active) queries, as a mounted panel's are.
    for (const observer of [
      new QueryObserver(client, { queryKey: ['plain'], queryFn: plain }),
      new QueryObserver(client, { queryKey: ['audited'], queryFn: audited, meta: { auditedPages: true } }),
      new QueryObserver(client, { queryKey: ['answered'], queryFn: answered }),
      new QueryObserver(client, { queryKey: ['unreviewed'], queryFn: unreviewed }),
      new QueryObserver(client, { queryKey: ['audit-free'], queryFn: auditFree }),
    ]) {
      cleanups.push(observer.subscribe(() => undefined));
    }
    cleanups.push(() => client.clear());
    await vi.waitFor(() => {
      for (const key of ['plain', 'audited', 'answered', 'unreviewed', 'audit-free']) {
        expect(client.getQueryState([key])?.status).toBe('error');
      }
    });

    refetchUnreachableQueries(client);
    await vi.waitFor(() => expect(plain).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(auditFree).toHaveBeenCalledTimes(2));
    expect(audited).toHaveBeenCalledTimes(1);
    expect(answered).toHaveBeenCalledTimes(1);
    expect(unreviewed).toHaveBeenCalledTimes(1);
  });

  it('never re-reads a query that opted out of the reconnect refetch, whatever its failure', async () => {
    // The Offer detail's snapshot fans out: GET /borrowers/{id} (VIEW_BORROWER)
    // was answered while POST /offers/recommend never was, so its error reads
    // "never answered" though its audit row is written. Its own
    // refetchOnReconnect: false keeps it out of this recovery as it does a reconnect.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const neverReached = () =>
      Promise.reject(new ApiError('unreachable', { path: '/api/v1/offers/recommend', method: 'POST', reason: 'unreachable' }));
    const bodyLost = () =>
      Promise.reject(new ApiError('unreachable', { path: '/api/v1/analytics/executive', method: 'GET', reason: 'unreachable', status: 200 }));
    const snapshot = vi.fn(neverReached);
    const optedOutAuditFree = vi.fn(bodyLost);
    const plain = vi.fn(neverReached);
    for (const observer of [
      new QueryObserver(client, { queryKey: ['snapshot'], queryFn: snapshot, refetchOnReconnect: false }),
      new QueryObserver(client, { queryKey: ['opted-out'], queryFn: optedOutAuditFree, refetchOnReconnect: false }),
      new QueryObserver(client, { queryKey: ['plain'], queryFn: plain }),
    ]) {
      cleanups.push(observer.subscribe(() => undefined));
    }
    cleanups.push(() => client.clear());
    await vi.waitFor(() => {
      for (const key of ['snapshot', 'opted-out', 'plain']) {
        expect(client.getQueryState([key])?.status).toBe('error');
      }
    });

    refetchUnreachableQueries(client);
    // The control: the same failure on a query that keeps the default is completed.
    await vi.waitFor(() => expect(plain).toHaveBeenCalledTimes(2));
    expect(snapshot).toHaveBeenCalledTimes(1);
    expect(optedOutAuditFree).toHaveBeenCalledTimes(1);
  });

  it('completes an opted-out query declared one audit-free read, and only while its failed request is on the list', async () => {
    // The approval-request list, Genie history and its deep link, and the
    // revoke panel's lifecycle read opt out of the reconnect refetch but are
    // one audit-free GET each (meta.oneAuditFreeRead), so "Connection lost"
    // clearing completes them, as it did before PR #326.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const failing = (path: string, method: string, status: number | null = null) => () =>
      Promise.reject(new ApiError('unreachable', { path, method, reason: 'unreachable', status }));
    const listNeverReached = vi.fn(failing('/api/v1/outreach/approval-requests?scope=mine', 'GET'));
    const historyBodyLost = vi.fn(failing('/api/v1/genie/sessions', 'GET', 200));
    // Declared, but the request that failed is audited: the declaration is wrong, so it is ignored.
    const declaredAudited = vi.fn(failing('/api/v1/borrowers/B-P5YP9ESW32R7Z/proof', 'GET'));
    // The Offer snapshot fans out and declares nothing: still never re-read.
    const snapshot = vi.fn(failing('/api/v1/offers/recommend', 'POST'));
    const declared = { meta: { oneAuditFreeRead: true }, refetchOnReconnect: false } as const;
    for (const observer of [
      new QueryObserver(client, { queryKey: ['approval-requests'], queryFn: listNeverReached, ...declared }),
      new QueryObserver(client, { queryKey: ['genie-sessions'], queryFn: historyBodyLost, ...declared }),
      new QueryObserver(client, { queryKey: ['declared-audited'], queryFn: declaredAudited, ...declared }),
      new QueryObserver(client, { queryKey: ['snapshot'], queryFn: snapshot, refetchOnReconnect: false }),
    ]) {
      cleanups.push(observer.subscribe(() => undefined));
    }
    cleanups.push(() => client.clear());
    await vi.waitFor(() => {
      for (const key of ['approval-requests', 'genie-sessions', 'declared-audited', 'snapshot']) {
        expect(client.getQueryState([key])?.status).toBe('error');
      }
    });

    refetchUnreachableQueries(client);
    await vi.waitFor(() => expect(listNeverReached).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(historyBodyLost).toHaveBeenCalledTimes(2));
    expect(declaredAudited).toHaveBeenCalledTimes(1);
    expect(snapshot).toHaveBeenCalledTimes(1);
  });

  it('through the real transport: the approval-request list that never reached the server waits out a reconnect, then "Connection lost" completes it', async () => {
    let attempts = 0;
    const fetchMock = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw new TypeError('Failed to fetch');
      return new Response(JSON.stringify({ batches: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    const client = createMipQueryClient();
    client.mount();
    onlineManager.setOnline(true);
    const list = new QueryObserver(client, {
      queryKey: ['mip', 'approval-requests', 'mine', 'never-reached'],
      queryFn: ({ signal }) => getJson('/api/outreach/approval-requests?scope=mine', signal),
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
      meta: { oneAuditFreeRead: true },
    });
    cleanups.push(list.subscribe(() => undefined));
    cleanups.push(() => {
      client.unmount();
      client.clear();
      onlineManager.setOnline(true);
      vi.unstubAllGlobals();
    });
    await vi.waitFor(() => expect(list.getCurrentResult().status).toBe('error'));
    const error = list.getCurrentResult().error as ApiError;
    expect([error.status, error.method]).toEqual([null, 'GET']);

    // A reconnect is not an open: TanStack honours the observer's opt-out.
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    refetchUnreachableQueries(client);
    await vi.waitFor(() => expect(list.getCurrentResult().status).toBe('success'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('isOneAuditFreeRead', () => {
  const inState = (error: unknown, meta?: Record<string, unknown>) =>
    ({ state: { error }, meta }) as unknown as Pick<Query, 'state' | 'meta'>;
  const lost = (path: string, method: string | null = 'GET', status: number | null = null, reason = 'unreachable') =>
    new ApiError('failed', { path, method, reason, status });
  const declared = { oneAuditFreeRead: true };

  it('holds only for a declared query whose network-lost request is a reviewed audit-free read', () => {
    expect(isOneAuditFreeRead(inState(lost('/api/v1/outreach/approval-requests?scope=open'), declared))).toBe(true);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/genie/sessions/conv-1', 'GET', 200), declared))).toBe(true);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/borrowers/B-1/lifecycle', 'GET', null, 'offline'), declared))).toBe(true);
    // Undeclared, or declared with anything but true.
    expect(isOneAuditFreeRead(inState(lost('/api/v1/outreach/approval-requests')))).toBe(false);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/outreach/approval-requests'), { oneAuditFreeRead: 'yes' }))).toBe(false);
    // The failed request is audited, unreviewed, the wrong method, or names no method (fail-closed).
    expect(isOneAuditFreeRead(inState(lost('/api/v1/borrowers/B-1/proof'), declared))).toBe(false);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/leads?limit=5'), declared))).toBe(false);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/never-reviewed'), declared))).toBe(false);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/outreach/approval-requests', 'POST'), declared))).toBe(false);
    expect(isOneAuditFreeRead(inState(lost('/api/v1/outreach/approval-requests', null), declared))).toBe(false);
    // Not a network loss: the recoveries never take it.
    expect(isOneAuditFreeRead(inState(lost('/api/v1/genie/sessions', 'GET', 503, 'warming_up'), declared))).toBe(false);
    expect(isOneAuditFreeRead(inState(null, declared))).toBe(false);
  });

  it('is declared only by the reviewed single audit-free reads', () => {
    // A query that declares it must send ONE request, to a route on
    // lib/auditFreeReads: a query that fans out (the Offer snapshot) may have
    // had an audited request answered beside the one that failed. Adding a
    // declaration is a review: list the file here.
    const src = join(process.cwd(), 'src');
    const declaring = (readdirSync(src, { recursive: true }) as string[])
      .map((entry) => entry.split('\\').join('/'))
      .filter((entry) => /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && !entry.startsWith('test/'))
      .filter((entry) => (readFileSync(join(src, entry), 'utf8') as string).includes('oneAuditFreeRead'))
      .sort();
    expect(declaring).toEqual([
      'components/connectionState.ts',
      'components/mortgage/GenieHistoryMenu.tsx',
      'components/mortgage/RevokeApprovalPanel.tsx',
      'routes/ask-genie.deep-link.tsx',
      'routes/lead-queue.approvalRequests.ts',
    ]);
    for (const entry of declaring.filter((path) => path !== 'components/connectionState.ts')) {
      const text = readFileSync(join(src, entry), 'utf8') as string;
      expect(text.split('meta: { oneAuditFreeRead: true }').length - 1, entry).toBe(1);
      expect(text.split('oneAuditFreeRead').length - 1, entry).toBe(1);
    }
  });
});

describe('optedOutOfReconnect', () => {
  it('is true when an observer set refetchOnReconnect === false', () => {
    const observedWith = (...values: unknown[]) =>
      ({ observers: values.map((refetchOnReconnect) => ({ options: { refetchOnReconnect } })) }) as unknown as Pick<
        Query,
        'observers'
      >;
    expect(optedOutOfReconnect(observedWith(false))).toBe(true);
    // Any observer that opted out keeps the query out (fail-closed).
    expect(optedOutOfReconnect(observedWith(undefined, false))).toBe(true);
    expect(optedOutOfReconnect(observedWith(true))).toBe(false);
    expect(optedOutOfReconnect(observedWith('always'))).toBe(false);
    expect(optedOutOfReconnect(observedWith(() => false))).toBe(false);
    expect(optedOutOfReconnect(observedWith(undefined))).toBe(false);
    expect(optedOutOfReconnect(observedWith())).toBe(false);
  });
});

describe('neverAnswered', () => {
  const inState = (error: unknown) => ({ state: { error } }) as unknown as Pick<Query, 'state'>;
  const failed = (reason: string, status: number | null = null, aborted = false) =>
    new ApiError('failed', { path: '/api/x', reason, status, aborted });

  it('is true only for an offline or unreachable failure that never got a status', () => {
    expect(neverAnswered(inState(failed('offline')))).toBe(true);
    expect(neverAnswered(inState(failed('unreachable')))).toBe(true);
    expect(neverAnswered(inState(failed('unreachable', 200)))).toBe(false);
    expect(neverAnswered(inState(failed('offline', 200)))).toBe(false);
    expect(neverAnswered(inState(failed('unreachable', null, true)))).toBe(false);
    expect(neverAnswered(inState(failed('unreadable_response', 200)))).toBe(false);
    expect(neverAnswered(inState(failed('warming_up', 503)))).toBe(false);
    expect(neverAnswered(inState(new TypeError('Failed to fetch')))).toBe(false);
    expect(neverAnswered(inState(null))).toBe(false);
  });
});

describe('answeredAuditFreeLoss', () => {
  const inState = (error: unknown) => ({ state: { error } }) as unknown as Pick<Query, 'state'>;
  const lost = (path: string, reason = 'unreachable', status: number | null = 200, aborted = false, method: string | null = 'GET') =>
    new ApiError('failed', { path, method, reason, status, aborted });

  it('is true only for a reviewed audit-free read the server answered whose body the network lost', () => {
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/analytics/executive')))).toBe(true);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/segments?states=CA', 'offline')))).toBe(true);
    // Audited, or never reviewed (fail-closed).
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/leads?limit=5')))).toBe(false);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/borrowers/B-P5YP9ESW32R7Z')))).toBe(false);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/audit/events/page?limit=25')))).toBe(false);
    expect(answeredAuditFreeLoss(inState(lost('/api/x')))).toBe(false);
    // Never answered (neverAnswered owns it), aborted, or not a network loss.
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/analytics/executive', 'unreachable', null)))).toBe(false);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/analytics/executive', 'unreachable', 200, true)))).toBe(false);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/analytics/executive', 'unreadable_response')))).toBe(false);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/analytics/executive', 'warming_up', 503)))).toBe(false);
    expect(answeredAuditFreeLoss(inState(null))).toBe(false);
  });

  it('judges the method the request used: a reviewed POST read, never a POST to a listed GET path', () => {
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/genie/start', 'unreachable', 200, false, 'POST')))).toBe(true);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/audit/my-events?limit=8')))).toBe(true);
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/workspace/saved-views', 'unreachable', 200, false, 'POST')))).toBe(false);
    // No method recorded: fail-closed.
    expect(answeredAuditFreeLoss(inState(lost('/api/v1/analytics/executive', 'unreachable', 200, false, null)))).toBe(false);
  });
});
