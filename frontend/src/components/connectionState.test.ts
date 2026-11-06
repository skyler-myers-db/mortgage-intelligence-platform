import { QueryClient, QueryObserver, type Query } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import { hasAuditedPages, neverAnswered, refetchUnreachableQueries } from './connectionState';

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

  it('re-reads an unreachable active query, never an audited paged one or one the server answered', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const unreachable = () => Promise.reject(new ApiError('unreachable', { path: '/api/x', reason: 'unreachable' }));
    // A 200 whose body was lost mid-transfer (apiTransport _readJson): served.
    const bodyLost = () => Promise.reject(new ApiError('unreachable', { path: '/api/x', reason: 'unreachable', status: 200 }));
    const plain = vi.fn(unreachable);
    const audited = vi.fn(unreachable);
    const answered = vi.fn(bodyLost);
    // Observed (active) queries, as a mounted panel's are.
    for (const observer of [
      new QueryObserver(client, { queryKey: ['plain'], queryFn: plain }),
      new QueryObserver(client, { queryKey: ['audited'], queryFn: audited, meta: { auditedPages: true } }),
      new QueryObserver(client, { queryKey: ['answered'], queryFn: answered }),
    ]) {
      cleanups.push(observer.subscribe(() => undefined));
    }
    cleanups.push(() => client.clear());
    await vi.waitFor(() => {
      expect(client.getQueryState(['plain'])?.status).toBe('error');
      expect(client.getQueryState(['audited'])?.status).toBe('error');
      expect(client.getQueryState(['answered'])?.status).toBe('error');
    });

    refetchUnreachableQueries(client);
    await vi.waitFor(() => expect(plain).toHaveBeenCalledTimes(2));
    expect(audited).toHaveBeenCalledTimes(1);
    expect(answered).toHaveBeenCalledTimes(1);
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
