// @vitest-environment happy-dom
import { QueryClient } from '@tanstack/react-query';
import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FollowupList, LeadSnoozeRequest, LeadSnoozeResponse } from '../apiClients/followUps';
import { ApiError } from '../apiTransport';
import { clearToasts, getToasts } from '../toast';
import { mount } from '../../test/render';

const snoozePost = vi.hoisted(() => ({ fn: vi.fn() }));
vi.mock('../apiClients/followUps', () => ({
  fetchFollowupList: () => Promise.reject(new Error('not used')),
  postLeadSnooze: (borrowerId: string, body: unknown) => snoozePost.fn(borrowerId, body),
}));

import { SNOOZE_NOT_CONTACTABLE, SNOOZE_NOT_OPEN, snoozedToastTitle, useSnoozeFollowUp } from './followUps';

/**
 * The Snooze write (wow-power-3 phase 2): a closed body, one request_id per
 * intent reused on retry, pessimistic (nothing moves before the 2xx), the
 * follow-ups keys invalidated on success, and the 409 / 429 branches.
 */

const BORROWER = 'B-0000000000001';
const OK: LeadSnoozeResponse = {
  borrower_id: BORROWER,
  wake_at: '2026-10-10T15:00:00Z',
  wake_clamped: false,
  audit_event_id: '11111111-1111-4111-8111-111111111111',
};
const LIST: FollowupList = { items: [], generated_at: '2026-10-07T15:00:00Z', truncated: false };

let client: QueryClient;
let current: () => ReturnType<typeof useSnoozeFollowUp> = () => {
  throw new Error('hook did not render');
};
const hook = {
  snooze: (...args: Parameters<ReturnType<typeof useSnoozeFollowUp>['snooze']>) => current().snooze(...args),
  get pending() {
    return current().pending;
  },
};

/** The hook's latest render, read through a closure (no module-level reassignment in render). */
async function render(): Promise<void> {
  let value: ReturnType<typeof useSnoozeFollowUp> | undefined;
  function Probe() {
    value = useSnoozeFollowUp(client);
    return null;
  }
  current = () => {
    if (value === undefined) throw new Error('hook did not render');
    return value;
  };
  await mount(createElement(Probe));
}

function sentBodies(): LeadSnoozeRequest[] {
  return snoozePost.fn.mock.calls.map((call) => call[1] as LeadSnoozeRequest);
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  snoozePost.fn.mockReset();
  clearToasts();
});
afterEach(() => {
  client.clear();
  clearToasts();
});

describe('useSnoozeFollowUp', () => {
  it('sends exactly {snooze_days, request_id} and invalidates the follow-ups keys on success', async () => {
    snoozePost.fn.mockResolvedValue(OK);
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await render();

    const result = await hook.snooze(BORROWER, 3);

    expect(result).toEqual(OK);
    expect(snoozePost.fn).toHaveBeenCalledTimes(1);
    expect(snoozePost.fn.mock.calls[0][0]).toBe(BORROWER);
    const [body] = sentBodies();
    expect(Object.keys(body).sort()).toEqual(['request_id', 'snooze_days']);
    expect(body.snooze_days).toBe(3);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['mip', 'followups'] });
    const [toast] = getToasts();
    expect(toast).toMatchObject({ tone: 'success', title: snoozedToastTitle(OK), auditEventId: OK.audit_event_id });
  });

  it('says when the contact frequency cap moved the wake time', () => {
    expect(snoozedToastTitle({ ...OK, wake_clamped: true })).toMatch(/^Snoozed until .+ \(contact frequency cap\)$/);
    expect(snoozedToastTitle(OK)).not.toContain('frequency cap');
  });

  it('reuses the request_id on a retry of the same intent and mints a new one after success', async () => {
    snoozePost.fn
      .mockRejectedValueOnce(new ApiError('boom', { path: '/x', status: 500 }))
      .mockResolvedValueOnce(OK)
      .mockResolvedValueOnce(OK);
    await render();

    await hook.snooze(BORROWER, 7);
    await hook.snooze(BORROWER, 7);
    await hook.snooze(BORROWER, 7);

    const ids = sentBodies().map((body) => body.request_id);
    expect(ids[0]).toBe(ids[1]);
    expect(ids[2]).not.toBe(ids[1]);
  });

  it('is pessimistic: the list cache is untouched while the request is in flight', async () => {
    let resolve: (value: LeadSnoozeResponse) => void = () => undefined;
    snoozePost.fn.mockReturnValue(new Promise<LeadSnoozeResponse>((done) => { resolve = done; }));
    client.setQueryData(['mip', 'followups', 'list', 7], LIST);
    await render();

    const pending = hook.snooze(BORROWER, 1);
    await vi.waitFor(() => expect(hook.pending).toMatchObject({ borrowerId: BORROWER, snoozeDays: 1 }));
    expect(client.getQueryData(['mip', 'followups', 'list', 7])).toBe(LIST);
    expect(client.getQueryState(['mip', 'followups', 'list', 7])?.isInvalidated).toBe(false);

    resolve(OK);
    await pending;
    expect(client.getQueryState(['mip', 'followups', 'list', 7])?.isInvalidated).toBe(true);
  });

  it.each([
    ['not_contactable', SNOOZE_NOT_CONTACTABLE],
    ['no_open_follow_up', SNOOZE_NOT_OPEN],
  ])('answers a 409 %s with plain copy and re-reads the list', async (detail, copy) => {
    snoozePost.fn.mockRejectedValue(new ApiError(detail, { path: '/x', status: 409 }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await render();

    expect(await hook.snooze(BORROWER, 3)).toBeNull();

    expect(getToasts()).toMatchObject([{ tone: 'error', title: copy }]);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['mip', 'followups'] });
  });

  it('answers a 429 with a Retry that waits out Retry-After and resends the same request_id', async () => {
    snoozePost.fn
      .mockRejectedValueOnce(new ApiError('rate limited', { path: '/x', status: 429, retryable: true, retryAfterMs: 4000 }))
      .mockResolvedValueOnce(OK);
    await render();

    expect(await hook.snooze(BORROWER, 1)).toBeNull();
    const [toast] = getToasts();
    expect(toast.tone).toBe('info');
    expect(toast.action?.label).toBe('Retry');
    expect(toast.actionReadyAt).not.toBeNull();

    toast.action?.onAction();
    await vi.waitFor(() => expect(snoozePost.fn).toHaveBeenCalledTimes(2));
    const [first, second] = sentBodies();
    expect(second.request_id).toBe(first.request_id);
  });

  it('describes any other failure without the server message', async () => {
    snoozePost.fn.mockRejectedValue(new ApiError('statement_id=abc secret detail', { path: '/x', status: 500 }));
    await render();

    await hook.snooze(BORROWER, 1);

    const [toast] = getToasts();
    expect(toast.tone).toBe('error');
    expect(`${toast.title} ${toast.detail}`).not.toContain('secret detail');
  });
});
