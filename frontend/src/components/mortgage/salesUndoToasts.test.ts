/**
 * @vitest-environment happy-dom
 *
 * C5 (W5d check correction 33): the sales writes' Retry toasts are the
 * queue's one retry form, toastWriteFailure(title, err, subject, retry):
 * a 429 that names a wait keeps one Retry, held behind the countdown, that
 * re-runs the SAME intent; anything else has no action; the toast detail is
 * describeApiError's sentence. salesUndoRun.raiseRetryToast is gone, and a
 * rate-limited Undo takes the same path.
 */
import { QueryClient } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import { clearToasts, getToasts } from '../../lib/toast';
import type { LeadAssignment } from '../../types';

const releaseAssignmentWrite = vi.hoisted(() => vi.fn());
vi.mock('../../lib/mutations/salesLoop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/mutations/salesLoop')>()),
  releaseAssignmentWrite,
}));

import * as salesUndoRun from './salesUndoRun';
import { toastSalesWriteFailure } from './salesUndoToasts';

const throttled = (retryAfterMs: number | null) => new ApiError('Too many requests: raw internals', {
  path: '/api/sales/assign', status: 429, retryAfterMs,
});

async function settle(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

beforeAll(async () => {
  await import('../ui/AsyncFailure');
}, 60_000);

beforeEach(() => {
  clearToasts();
  releaseAssignmentWrite.mockReset();
});

afterEach(() => clearToasts());

describe('toastSalesWriteFailure', () => {
  it('keeps ONE Retry behind the countdown for a 429 that names a wait, and it re-runs the intent', async () => {
    const retry = vi.fn();
    toastSalesWriteFailure("Couldn't assign the leads", throttled(15_000), 'the assignment', retry);
    await settle();
    const toasts = getToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].title).toBe("Couldn't assign the leads");
    expect(toasts[0].action?.label).toBe('Retry');
    expect(toasts[0].actionReadyAt ?? 0).toBeGreaterThan(Date.now() + 10_000);
    expect(toasts[0].detail ?? '').not.toContain('raw internals');
    toasts[0].action!.onAction();
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('offers no action without a Retry-After, or for any other failure', async () => {
    const retry = vi.fn();
    toastSalesWriteFailure("Couldn't assign the leads", throttled(null), 'the assignment', retry);
    toastSalesWriteFailure("Couldn't distribute the leads", new ApiError('boom', { path: '/api/sales/distribute', status: 500 }), 'the distribution', retry);
    await settle();
    expect(getToasts().map((toast) => toast.action)).toEqual([null, null]);
    expect(retry).not.toHaveBeenCalled();
  });
});

describe('a rate-limited Undo', () => {
  it('raises the same Retry form, and its Retry re-sends the same release request id', async () => {
    const ids: string[] = [];
    releaseAssignmentWrite
      .mockImplementationOnce((_client: unknown, variables: { requestId: string }) => {
        ids.push(variables.requestId);
        return Promise.reject(throttled(20_000));
      })
      .mockImplementationOnce((_client: unknown, variables: { requestId: string }) => {
        ids.push(variables.requestId);
        return Promise.resolve({ released: { assignment_id: 'asg-1' }, restored: null, restore_skipped_reason: null, audit_event_id: 'audit-undo-1' });
      });
    const minted = new Map<string, string>();
    const ctx = {
      queryClient: new QueryClient(),
      requestIdFor: (fingerprint: string) => {
        if (!minted.has(fingerprint)) minted.set(fingerprint, `req-${minted.size + 1}`);
        return minted.get(fingerprint)!;
      },
      settle: (fingerprint: string) => minted.delete(fingerprint),
      onReverted: vi.fn(),
    };
    salesUndoRun.undoAssignment(ctx, { assignment_id: 'asg-1', borrower_id: 'B-0000000000UN1' } as LeadAssignment);
    await settle();
    expect('raiseRetryToast' in salesUndoRun).toBe(false);
    const [failure] = getToasts();
    expect(failure.title).toBe("Couldn't undo the assignment");
    expect(failure.action?.label).toBe('Retry');
    expect(failure.actionReadyAt ?? 0).toBeGreaterThan(Date.now() + 15_000);
    await act(async () => failure.action!.onAction());
    await settle();
    expect(ids).toEqual(['req-1', 'req-1']);
    expect(getToasts().some((toast) => toast.title === 'Assignment undone')).toBe(true);
  });
});
