/**
 * The Lead Queue's write-failure toast (states-08 item 3): a 429 with a
 * Retry-After and a retry carries ONE Retry action, held behind the shell
 * toast's countdown (actionReadyAt); without a Retry-After, or without a
 * retry, the toast is today's, with no action.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import { clearToasts, getToasts } from '../../lib/toast';
import { toastWriteFailure } from './leadWriteFailureToast';

const NOW = Date.UTC(2026, 9, 7, 12, 0, 0);

async function settled() {
  await vi.waitFor(() => expect(getToasts()).toHaveLength(1), { timeout: 10_000 });
  return getToasts()[0];
}

beforeAll(async () => {
  // The describeApiError sentence rides AsyncFailure's lazy chunk.
  await import('../ui/AsyncFailure');
}, 30_000);

beforeEach(() => {
  clearToasts();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  clearToasts();
});

describe('toastWriteFailure', () => {
  it('holds a 429 Retry behind its Retry-After countdown, and runs the retry once activated', async () => {
    const retry = vi.fn();
    toastWriteFailure("Couldn't approve B-1", new ApiError('Too many requests', { path: '/x', status: 429, retryAfterMs: 30_000 }), 'the approval', retry);
    const toast = await settled();
    expect(toast.tone).toBe('error');
    expect(toast.action?.label).toBe('Retry');
    expect(toast.actionReadyAt).toBeGreaterThanOrEqual(NOW + 30_000);
    expect(toast.actionReadyAt).toBeLessThan(NOW + 31_000);
    expect(retry).not.toHaveBeenCalled();
    toast.action!.onAction();
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('offers no action for a 429 without a Retry-After, or with no retry to run', async () => {
    toastWriteFailure("Couldn't approve B-1", new ApiError('Too many requests', { path: '/x', status: 429 }), 'the approval', vi.fn());
    expect((await settled()).action).toBeNull();
    clearToasts();
    toastWriteFailure("Couldn't reject B-1", new ApiError('Too many requests', { path: '/x', status: 429, retryAfterMs: 5_000 }), 'the rejection');
    expect((await settled()).action).toBeNull();
  });

  it('never offers a Retry for a failure that is not a 429', async () => {
    toastWriteFailure("Couldn't approve B-1", new ApiError('Internal Server Error', { path: '/x', status: 500, retryAfterMs: 5_000 }), 'the approval', vi.fn());
    const toast = await settled();
    expect(toast.action).toBeNull();
    expect(toast.actionReadyAt).toBeNull();
  });
});
