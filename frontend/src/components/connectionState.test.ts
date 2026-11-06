import { QueryClient, QueryObserver, type Query } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import { answeredAuditFreeLoss, hasAuditedPages, neverAnswered, refetchUnreachableQueries } from './connectionState';

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
      Promise.reject(new ApiError('unreachable', { path, reason: 'unreachable', status: 200 }));
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
  const lost = (path: string, reason = 'unreachable', status: number | null = 200, aborted = false) =>
    new ApiError('failed', { path, reason, status, aborted });

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
});
