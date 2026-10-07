/**
 * @vitest-environment happy-dom
 *
 * The failure toast of a receipt the user waited on (W5d): a 429 offers Try
 * again behind its Retry-After; a server failure reads describeApiError's
 * body; anything else a fixed sentence, never the raw error message.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';

const toastError = vi.hoisted(() => vi.fn());
vi.mock('../../lib/toast', () => ({ toast: { error: toastError, info: vi.fn(), success: vi.fn() } }));

import { RECEIPT_NOT_RECORDED, RECEIPT_RATE_LIMITED, toastReceiptFailure } from './receiptFailureToast';

// The vocabulary is its own chunk: transform it once up front.
beforeAll(async () => {
  await import('../ui/AsyncFailure');
}, 60_000);
beforeEach(() => toastError.mockReset());

describe('toastReceiptFailure', () => {
  it('a 429 holds Try again behind the Retry-After', () => {
    const retry = vi.fn();
    toastReceiptFailure('Print not recorded', new ApiError('slow down', { path: '/api/print-receipt', status: 429, retryAfterMs: 2500 }), 'the print receipt', retry);

    const [title, options] = toastError.mock.calls[0] as [string, { detail: string; retryAfterMs: number; action: { label: string; onAction: () => void } }];
    expect(title).toBe('Print not recorded');
    expect(options.detail).toBe(RECEIPT_RATE_LIMITED);
    expect(options.retryAfterMs).toBe(2500);
    expect(options.action.label).toBe('Try again');
    options.action.onAction();
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('a server failure reads the shared vocabulary, never the transport message', async () => {
    toastReceiptFailure(
      'Print not recorded',
      new ApiError('SENTINEL lakebase pool exhausted', { path: '/api/print-receipt', status: 503, dependency: 'lakebase', reason: 'retries_exhausted' }),
      'the print receipt',
      vi.fn(),
    );
    await vi.waitFor(() => expect(toastError).toHaveBeenCalledTimes(1), { timeout: 10_000 });
    const [, options] = toastError.mock.calls[0] as [string, { detail: string; action?: unknown }];
    expect(options.detail).not.toContain('SENTINEL');
    expect(options.detail.length).toBeGreaterThan(0);
    expect(options.action).toBeUndefined();
  });

  it('a non-API error gets the fixed sentence', () => {
    toastReceiptFailure('Audit page not exported', new TypeError('SENTINEL x is undefined'), 'the audit export receipt', vi.fn());
    expect(toastError).toHaveBeenCalledWith('Audit page not exported', { detail: RECEIPT_NOT_RECORDED });
  });
});
