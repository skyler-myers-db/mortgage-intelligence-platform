/**
 * @vitest-environment happy-dom
 *
 * The revoke panel (audit flow-v2; deviation:revoke-approval), rendered:
 *  - the lifecycle read runs only on an open that names no approval, once;
 *  - one POST per Confirm with the exact body, a synchronous latch, and the
 *    SAME request_id on a 429 retry;
 *  - pessimistic: the session's approval override goes only after the 200,
 *    and the success toast names the audit row;
 *  - every failure stays inline in closed copy that claims a record only for
 *    the governed 422 (owner ruling O1, W5e C9), never the raw error text;
 *  - the decision invalidation stale-marks the audited reads, never an
 *    active refetch.
 */
import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import { clearToasts, getToasts } from '../../lib/toast';
import { mount } from '../../test/render';
import { reportFocus, startReader, stopReader } from '../../test/screenReader/virtualReader';
import { AppStoreContext, createAppStore, type AppStore } from '../appStore';
import { RATIONALE_GOVERNED_REFUSAL } from '../../lib/apiClients/approvalRequests';
import { useRevokeApproval } from '../../lib/mutations/outreach';
import { describeWriteFailure, toastWriteFailure } from './leadWriteFailureToast';
import RevokeApprovalPanel, { REVOKE_COPY } from './RevokeApprovalPanel';
import {
  REVOKE_FAILED_LINE,
  REVOKE_FAILURE_COPY,
  REVOKE_GOVERNED_REFUSAL,
  REVOKE_NOT_RECORDED,
  REVOKE_REFUSAL_COPY,
} from './RevokeApprovalPanel.failure';

const revokeApproval = vi.hoisted(() => vi.fn());
const borrowerLifecycle = vi.hoisted(() => vi.fn());

vi.mock('../../lib/apiClients/outreachRevoke', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/apiClients/outreachRevoke')>()),
  revokeApproval,
}));
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return { ...actual, api: { ...actual.api, borrowerLifecycle } };
});

const BORROWER = 'B-0000000000RV1';
const APPROVAL = '6f1c4d2e-8b3a-4c5d-9e6f-7a8b9c0d1e2f';
const RATIONALE = 'Offer code was mis-keyed; needs a second review.';
const REVOKED = {
  revoked: true,
  approval_id: '1a2b3c4d-0000-4000-8000-000000000001',
  revoked_approval_id: APPROVAL,
  audit_event_id: 'audit-revoke-1',
  released_assignment_id: null,
};

let client: QueryClient;
let store: AppStore;
let onClose: ReturnType<typeof vi.fn<() => void>>;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

/** The shell's side: it owns the revoke mutation and hands it, with the toasts, to the panel. */
function Shell({ approvalId }: { approvalId: string | null }) {
  const revoke = useRevokeApproval(client);
  return (
    <RevokeApprovalPanel
      borrowerId={BORROWER}
      approvalId={approvalId}
      onClose={onClose}
      revoke={revoke}
      failureDeps={{ toastWriteFailure, describeWriteFailure }}
    />
  );
}

async function render(approvalId: string | null = APPROVAL) {
  const mounted = await mount(
    <QueryClientProvider client={client}>
      <AppStoreContext.Provider value={store}>
        <Shell approvalId={approvalId} />
      </AppStoreContext.Provider>
    </QueryClientProvider>,
  );
  await flush();
  return mounted;
}

const field = () => document.querySelector<HTMLTextAreaElement>('[data-testid="revoke-approval-reason"]');
const confirm = () => document.querySelector<HTMLButtonElement>('[data-testid="revoke-approval-confirm"]');
const failure = () => document.querySelector('[data-testid="revoke-approval-failure"]')?.textContent ?? null;

async function type(text: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
    setter.call(field(), text);
    field()!.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submit() {
  await act(async () => confirm()!.click());
  await flush();
}

function refused(status: number, message: string, extra: { reason?: string; retryAfterMs?: number } = {}) {
  return new ApiError(message, { path: '/api/outreach/revoke', status, ...extra });
}

beforeAll(async () => {
  // describeApiError's sentence rides AsyncFailure's lazy chunk, and the failure
  // copy rides its own: both transformed once up front.
  await import('../ui/AsyncFailure');
  await import('./RevokeApprovalPanel.failure');
}, 60_000);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  store = createAppStore();
  store.update('approvals', { [BORROWER]: 'approved' });
  onClose = vi.fn<() => void>();
  revokeApproval.mockReset();
  borrowerLifecycle.mockReset();
  clearToasts();
});

afterEach(() => {
  vi.useRealTimers();
  clearToasts();
});

describe('RevokeApprovalPanel: the form', () => {
  it('names the borrower, says what a revoke does, and asks for a governed reason', async () => {
    const { container } = await render();
    expect(container.querySelector('h3')?.textContent).toBe(`Revoke the approval for ${BORROWER}?`);
    expect(container.textContent).toContain(REVOKE_COPY.consequence);
    expect(field()!.closest('label')?.querySelector('.field__label')?.textContent).toBe('Reason for revoking');
    expect(field()!.maxLength).toBe(500);
    expect(field()!.required).toBe(true);
    expect(container.textContent).toContain('Leave out personal details.');
    expect(confirm()!.textContent).toBe('Revoke approval');
    expect(confirm()!.className).toContain('btn--danger');
    expect(document.activeElement).toBe(field());
    // The receipt path knows the approval: no lifecycle read at all.
    expect(borrowerLifecycle).not.toHaveBeenCalled();
  });

  it('names the reason field by its label alone; the hint is said once, as its description', async () => {
    await render();
    // The virtual screen reader (src/test/screenReader): the wrapping <label>
    // also holds the hint, which once made the name "Reason for revoking
    // Leave out personal details." with the hint read again as the description.
    const reader = await startReader();
    try {
      const spoken = await reader.step('report the focused reason field', reportFocus);
      expect(document.activeElement).toBe(field());
      expect(spoken).toEqual([`textbox, Reason for revoking, ${REVOKE_COPY.hint}, required`]);
    } finally {
      await stopReader();
    }
  });

  it('an empty Confirm moves focus to the reason and sends nothing', async () => {
    await render();
    (document.activeElement as HTMLElement | null)?.blur();
    await submit();
    expect(revokeApproval).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(field());
  });

  it('sends one POST with the exact body, then drops the override, toasts and closes (pessimistic)', async () => {
    let resolve!: (value: typeof REVOKED) => void;
    revokeApproval.mockImplementation(() => new Promise((settle) => {
      resolve = settle;
    }));
    await render();
    await type(`  ${RATIONALE}  `);
    await act(async () => {
      confirm()!.click();
      confirm()!.click();
    });
    await flush();
    expect(revokeApproval).toHaveBeenCalledTimes(1);
    const [body] = revokeApproval.mock.calls[0] as [Record<string, string>];
    expect(Object.keys(body).sort()).toEqual(['approval_id', 'borrower_id', 'rationale', 'request_id']);
    expect(body).toMatchObject({ borrower_id: BORROWER, approval_id: APPROVAL, rationale: RATIONALE });
    // On the wire: still Approved in the session, nothing toasted, the panel open.
    expect(store.getSnapshot().approvals[BORROWER]).toBe('approved');
    expect(getToasts()).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => resolve(REVOKED));
    await flush();
    expect(store.getSnapshot().approvals[BORROWER]).toBeUndefined();
    expect(getToasts().map((toast) => [toast.tone, toast.title, toast.auditEventId])).toEqual([
      ['info', `Approval revoked for ${BORROWER}`, 'audit-revoke-1'],
    ]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('says an unworked assignment was released', async () => {
    revokeApproval.mockResolvedValue({ ...REVOKED, released_assignment_id: 'asg-1' });
    await render();
    await type(RATIONALE);
    await submit();
    expect(getToasts()[0]?.detail).toBe('Its unworked assignment was released.');
  });

  it('stale-marks the audited reads after a revoke, never an active refetch', async () => {
    revokeApproval.mockResolvedValue(REVOKED);
    const leads = vi.fn(() => Promise.resolve([]));
    const borrower = vi.fn(() => Promise.resolve({}));
    const observers = [
      new QueryObserver(client, { queryKey: ['mip', 'leads', 'view'], queryFn: leads }),
      new QueryObserver(client, { queryKey: ['mip', 'borrower', BORROWER], queryFn: borrower }),
    ];
    const unsubscribe = observers.map((observer) => observer.subscribe(() => undefined));
    await flush();
    const before = [leads.mock.calls.length, borrower.mock.calls.length];
    await render();
    await type(RATIONALE);
    await submit();
    expect(client.getQueryState(['mip', 'leads', 'view'])?.isInvalidated).toBe(true);
    expect([leads.mock.calls.length, borrower.mock.calls.length]).toEqual(before);
    unsubscribe.forEach((off) => off());
  });
});

describe('RevokeApprovalPanel: the lifecycle read on open', () => {
  it('reads the lifecycle once on an open that names no approval, then revokes the approval it names', async () => {
    borrowerLifecycle.mockResolvedValue({
      borrower_id: BORROWER, approval_status: 'approved', outreach_status: 'queued', approval_id: APPROVAL,
    });
    revokeApproval.mockResolvedValue(REVOKED);
    await render(null);
    expect(borrowerLifecycle).toHaveBeenCalledTimes(1);
    expect(borrowerLifecycle.mock.calls[0][0]).toBe(BORROWER);
    await type(RATIONALE);
    await submit();
    expect((revokeApproval.mock.calls[0] as [Record<string, string>])[0].approval_id).toBe(APPROVAL);
    expect(borrowerLifecycle).toHaveBeenCalledTimes(1);
  });

  it.each([
    [{ approval_status: 'pending', outreach_status: 'none', approval_id: null }, REVOKE_COPY.noApproval],
    [{ approval_status: 'approved', outreach_status: 'sent', approval_id: APPROVAL }, REVOKE_COPY.outreachStarted],
  ])('shows why nothing can be revoked instead of the form (%o)', async (life, copy) => {
    borrowerLifecycle.mockResolvedValue({ borrower_id: BORROWER, ...life });
    const { container } = await render(null);
    expect(container.querySelector('[data-testid="revoke-approval-note"]')?.textContent).toBe(copy);
    expect(field()).toBeNull();
    expect(confirm()).toBeNull();
  });
});

describe('RevokeApprovalPanel: failures stay inline, in closed copy', () => {
  it.each(Object.entries(REVOKE_REFUSAL_COPY))('a 409 %s reads its own sentence and keeps the reason', async (reason, copy) => {
    revokeApproval.mockRejectedValue(refused(409, `server detail for ${reason}`, { reason }));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).toBe(copy);
    expect(failure()).not.toContain('server detail');
    expect(field()!.value).toBe(RATIONALE);
    expect(onClose).not.toHaveBeenCalled();
    // campaign_bound is final in v1: Confirm goes.
    expect(confirm() === null).toBe(reason === 'campaign_bound');
    expect(store.getSnapshot().approvals[BORROWER]).toBe('approved');
  });

  it('the governed 422 says the refusal was recorded', async () => {
    revokeApproval.mockRejectedValue(refused(422, 'rationale failed the governed text policy'));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).toBe(REVOKE_FAILURE_COPY.governed);
  });

  it("NB-1's unaudited 422 says nothing was recorded, never 'Shorten the reason'", async () => {
    revokeApproval.mockRejectedValue(refused(422, REVOKE_NOT_RECORDED));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).toBe(REVOKE_FAILURE_COPY.notRecorded);
    expect(failure()).not.toContain('recorded.');
    expect(failure()).not.toContain('Shorten');
  });

  it('any other 422 says to shorten the reason, and claims no record', async () => {
    revokeApproval.mockRejectedValue(refused(422, 'value too long'));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).toBe(REVOKE_FAILURE_COPY.invalid);
  });

  it('a changed actor says nothing was recorded', async () => {
    revokeApproval.mockRejectedValue(refused(409, 'actor changed', { reason: 'actor_changed' }));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).toBe(REVOKE_FAILURE_COPY.actorChanged);
  });

  it('a 503 never claims a record, and never shows the raw message', async () => {
    revokeApproval.mockRejectedValue(refused(503, 'lakebase unavailable: pool exhausted'));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).not.toBeNull();
    expect(failure()).not.toMatch(/recorded/i);
    expect(failure()).not.toContain('pool exhausted');
  });

  it('a non-API failure reads a fixed sentence', async () => {
    revokeApproval.mockRejectedValue(new TypeError('secret internals'));
    await render();
    await type(RATIONALE);
    await submit();
    expect(failure()).toBe(REVOKE_FAILED_LINE);
  });

  it('a 429 with Retry-After raises the shared toast; its Retry is inert until the countdown, then re-sends the SAME request id', async () => {
    revokeApproval
      .mockRejectedValueOnce(refused(429, 'slow down', { retryAfterMs: 2_000 }))
      .mockResolvedValueOnce(REVOKED);
    await render();
    await type(RATIONALE);
    await submit();
    await flush();
    const [toast] = getToasts();
    expect(toast?.tone).toBe('error');
    expect(toast?.title).toBe(REVOKE_FAILURE_COPY.failed);
    expect(toast?.action?.label).toBe('Retry');
    // The Toaster holds the action inert until this instant (the shared countdown).
    expect(toast?.actionReadyAt ?? 0).toBeGreaterThan(Date.now());
    expect(failure()).toBeNull();
    await act(async () => toast!.action!.onAction());
    await flush();
    expect(revokeApproval).toHaveBeenCalledTimes(2);
    const ids = revokeApproval.mock.calls.map((call) => (call[0] as Record<string, string>).request_id);
    expect(ids[1]).toBe(ids[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('RevokeApprovalPanel: copy parity', () => {
  it("the governed 422 detail is the approval-request client's (one server sentence, two copies pinned equal)", () => {
    expect(REVOKE_GOVERNED_REFUSAL).toBe(RATIONALE_GOVERNED_REFUSAL);
  });
});

describe('RevokeApprovalPanel: closing', () => {
  it('Cancel and Escape close it and return focus to the opener', async () => {
    const opener = document.body.appendChild(document.createElement('button'));
    opener.focus();
    await render();
    expect(document.activeElement).toBe(field());
    await act(async () => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});
