import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import { hasAuditedPages, refetchUnreachableQueries } from './connectionState';

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

  it('re-reads an unreachable active query, never an audited paged one', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const unreachable = () => Promise.reject(new ApiError('unreachable', { path: '/api/x', reason: 'unreachable' }));
    const plain = vi.fn(unreachable);
    const audited = vi.fn(unreachable);
    // Observed (active) queries, as a mounted panel's are.
    for (const observer of [
      new QueryObserver(client, { queryKey: ['plain'], queryFn: plain }),
      new QueryObserver(client, { queryKey: ['audited'], queryFn: audited, meta: { auditedPages: true } }),
    ]) {
      cleanups.push(observer.subscribe(() => undefined));
    }
    cleanups.push(() => client.clear());
    await vi.waitFor(() => {
      expect(client.getQueryState(['plain'])?.status).toBe('error');
      expect(client.getQueryState(['audited'])?.status).toBe('error');
    });

    refetchUnreachableQueries(client);
    await vi.waitFor(() => expect(plain).toHaveBeenCalledTimes(2));
    expect(audited).toHaveBeenCalledTimes(1);
  });
});
