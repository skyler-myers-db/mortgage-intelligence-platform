/**
 * @vitest-environment happy-dom
 *
 * states-08 item 3 slice 6c: sample drafts the server rate-limited (429 with
 * a Retry-After) raise ONE shell toast whose Retry stays inert until the
 * countdown ends, then re-requests only the failed samples, once. Each
 * sample's inline error is describeApiError's sentence, never the raw
 * message. Every draft writes a DRAFT_OUTREACH row, so nothing re-drafts by
 * itself.
 */
import { act } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { ApiError } from '../../lib/apiTransport';
import { clearToasts, getToasts } from '../../lib/toast';
import { mount } from '../../test/render';
import { LeadBulkApproveReview, SAMPLE_DRAFTS_THROTTLED } from './LeadBulkApproveReview';
import { describeWriteFailure, toastWriteFailure } from './leadWriteFailureToast';

function lead(borrowerId: string, offer: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    recommended_offer_code: offer,
    recommended_offer: offer,
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LEADS = [lead('B-0000000000SA1', 'refi'), lead('B-0000000000SA2', 'heloc'), lead('B-0000000000SA3', 'cash_out')];

function draft(borrowerId: string): OutreachDraftResult {
  return { generation_id: `gen-${borrowerId}`, subject: `Subject ${borrowerId}`, body: 'Body' } as unknown as OutreachDraftResult;
}

const throttled = (retryAfterMs: number | null) => new ApiError('Too many requests: raw internals', {
  path: '/api/outreach/draft', status: 429, retryAfterMs,
});

async function flush() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

let draftForApproval: ReturnType<typeof vi.fn>;

async function render() {
  const mounted = await mount(
    <LeadBulkApproveReview
      leads={LEADS}
      canStartApproval={() => true}
      draftForApproval={draftForApproval as unknown as (id: string, signal?: AbortSignal) => Promise<OutreachDraftResult>}
      onSamplesChange={() => undefined}
      writeFailure={{ toastWriteFailure, describeWriteFailure }}
    />,
  );
  await act(async () => document.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-preview-samples"]')!.click());
  await flush();
  return mounted;
}

beforeAll(async () => {
  await import('../ui/AsyncFailure');
}, 60_000);

beforeEach(() => {
  clearToasts();
  draftForApproval = vi.fn();
});

afterEach(() => clearToasts());

describe('LeadBulkApproveReview: rate-limited sample drafts', () => {
  it('raises ONE toast for every throttled sample, with a Retry held behind the countdown', async () => {
    draftForApproval.mockImplementation((id: string) => (id === 'B-0000000000SA2'
      ? Promise.resolve(draft(id))
      : Promise.reject(throttled(30_000))));
    const { container } = await render();
    const toasts = getToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].title).toBe(SAMPLE_DRAFTS_THROTTLED);
    expect(toasts[0].action?.label).toBe('Retry');
    expect(toasts[0].actionReadyAt ?? 0).toBeGreaterThan(Date.now() + 20_000);
    // The inline errors: the described sentence, never the transport message.
    const errors = [...container.querySelectorAll('[role="alert"]')].map((node) => node.textContent ?? '');
    expect(errors).toHaveLength(2);
    for (const text of errors) {
      expect(text).not.toContain('raw internals');
      expect(text.length).toBeGreaterThan(0);
    }
  });

  it('the Retry re-requests only the failed samples, and only once', async () => {
    draftForApproval.mockImplementation((id: string) => (id === 'B-0000000000SA2'
      ? Promise.resolve(draft(id))
      : Promise.reject(throttled(30_000))));
    await render();
    expect(draftForApproval).toHaveBeenCalledTimes(3);
    const [first] = getToasts();
    clearToasts();
    await act(async () => first.action!.onAction());
    await flush();
    const retried = draftForApproval.mock.calls.slice(3).map((call) => call[0]).sort();
    expect(retried).toEqual(['B-0000000000SA1', 'B-0000000000SA3']);
    // Throttled again: the toast says so, with no second Retry.
    const again = getToasts();
    expect(again).toHaveLength(1);
    expect(again[0].action).toBeNull();
  });

  it('raises no toast for a 429 without a Retry-After, or any other failure', async () => {
    draftForApproval.mockImplementation((id: string) => (id === 'B-0000000000SA1'
      ? Promise.reject(throttled(null))
      : Promise.reject(new ApiError('boom', { path: '/api/outreach/draft', status: 500 }))));
    const { container } = await render();
    expect(getToasts()).toEqual([]);
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(3);
    expect(container.textContent).not.toContain('boom');
  });
});
