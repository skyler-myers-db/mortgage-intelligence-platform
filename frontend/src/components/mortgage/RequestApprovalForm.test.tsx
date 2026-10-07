/**
 * @vitest-environment happy-dom
 *
 * The Request approval form (audit flow-02 / shell-06, D-approval-flow-c),
 * at the rendered layer: the governed "Reason for the request" (O3, never
 * "Note"), one POST per send with one request_key per intent, nothing
 * drafted, the toast only after the 200, and failure copy that never claims
 * a record the server did not write (O1, W5d C3 / C4).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { approvalRequestsApi } from '../../lib/apiClients/approvalRequests';
import { ApiError } from '../../lib/apiTransport';
import { clearToasts, getToasts } from '../../lib/toast';
import { mount } from '../../test/render';
import type { ApprovalRequestCreated } from '../../types/approvalRequests';
import { RequestApprovalForm, REQUEST_FAILURE_COPY, skippedSummary } from './RequestApprovalForm';

const IDS = ['B-REQFORM000001', 'B-REQFORM000002', 'B-REQFORM000003'];
const CREATED: ApprovalRequestCreated = {
  batch_id: '0f8fad5b-d9cb-469f-a165-70867728950e',
  audit_event_id: 'audit-req-1',
  requested: [IDS[0], IDS[1]],
  skipped: [{ borrower_id: IDS[2], reason: 'already_requested' }],
};

let client: QueryClient;
let onCancel: ReturnType<typeof vi.fn<() => void>>;
let onRequested: ReturnType<typeof vi.fn<(created: ApprovalRequestCreated) => void>>;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function render(ids: readonly string[] = IDS) {
  return mount(
    <QueryClientProvider client={client}>
      <RequestApprovalForm borrowerIds={ids} onCancel={onCancel} onRequested={onRequested} />
    </QueryClientProvider>,
  );
}

const field = () => document.querySelector<HTMLTextAreaElement>('[data-testid="request-approval-reason"]')!;
const send = () => document.querySelector<HTMLButtonElement>('[data-testid="request-approval-send"]')!;
const failure = () => document.querySelector('[data-testid="request-approval-failure"]')?.textContent ?? null;

async function type(text: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
    setter.call(field(), text);
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submit() {
  await act(async () => send().click());
  await flush();
}

beforeAll(async () => {
  // describeApiError's sentence rides AsyncFailure's lazy chunk: transformed once up front.
  await import('../ui/AsyncFailure');
}, 60_000);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  onCancel = vi.fn<() => void>();
  onRequested = vi.fn<(created: ApprovalRequestCreated) => void>();
  clearToasts();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  clearToasts();
});

describe('RequestApprovalForm', () => {
  it('labels the field "Reason for the request" (O3), never "Note", and focuses it on open', async () => {
    const { container } = await render();
    const label = field().closest('label')!;
    expect(label.querySelector('.field__label')?.textContent).toBe('Reason for the request');
    expect(container.textContent).not.toMatch(/\bNote\b/);
    expect(field().required).toBe(true);
    expect(field().maxLength).toBe(500);
    expect(document.activeElement).toBe(field());
    expect(container.textContent).toContain('Request approval for 3 borrowers');
    expect(container.textContent).toContain("Nothing is sent. An approver reviews each borrower's copy before anything is approved.");
  });

  it('needs a reason: an empty send moves focus to the field and sends nothing', async () => {
    const create = vi.spyOn(approvalRequestsApi, 'create');
    await render();
    expect(send().getAttribute('aria-disabled')).toBe('true');
    send().focus();
    await submit();
    expect(create).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(field());
  });

  it('sends exactly one POST with the ids, the reason and a request_key, and never drafts', async () => {
    const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown> });
      return new Response(JSON.stringify(CREATED), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    await render();
    await type('  Rate-sensitive refinance candidates in our footprint.  ');
    await submit();
    expect(calls.map((call) => call.path)).toEqual(['/api/v1/outreach/approval-requests']);
    expect(calls[0].body).toEqual({
      borrower_ids: IDS,
      rationale: 'Rate-sensitive refinance candidates in our footprint.',
      request_key: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    expect(calls.some((call) => call.path.includes('/outreach/draft'))).toBe(false);
  });

  it('is pessimistic: no toast and no close before the 200; then the toast carries the audit id and the skips', async () => {
    let release: (value: ApprovalRequestCreated) => void = () => undefined;
    vi.spyOn(approvalRequestsApi, 'create').mockImplementation(() => new Promise((resolve) => {
      release = resolve;
    }));
    await render();
    await type('Rate-sensitive refinance candidates.');
    await submit();
    expect(getToasts()).toEqual([]);
    expect(onRequested).not.toHaveBeenCalled();
    expect(send().getAttribute('aria-busy')).toBe('true');
    await act(async () => release(CREATED));
    await flush();
    expect(onRequested).toHaveBeenCalledWith(CREATED);
    const [toast] = getToasts();
    expect(toast.tone).toBe('success');
    expect(toast.title).toBe('Approval requested for 2 borrowers');
    expect(toast.auditEventId).toBe('audit-req-1');
    expect(toast.detail).toBe('1 skipped: 1 already requested.');
  });

  it('reuses the request_key when the same content is sent again, and mints a new one for new content', async () => {
    const create = vi.spyOn(approvalRequestsApi, 'create').mockRejectedValue(
      new ApiError('The server could not complete this request.', { path: '/api/v1/outreach/approval-requests', status: 503 }),
    );
    await render();
    await type('Rate-sensitive refinance candidates.');
    await submit();
    await submit();
    await type('A different reason.');
    await submit();
    const keys = create.mock.calls.map(([body]) => body.request_key);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  const classified: Array<[string, ApiError, string]> = [
    ['actor_changed', new ApiError('Actor changed', { path: '/x', status: 409, reason: 'actor_changed' }), REQUEST_FAILURE_COPY.actor_changed],
    ['a governed 422', new ApiError('rationale failed the governed text policy', { path: '/x', status: 422 }), REQUEST_FAILURE_COPY.governed],
    ['another 422', new ApiError('rationale is too long once personal details are redacted', { path: '/x', status: 422 }), REQUEST_FAILURE_COPY.invalid],
    ['the zero-eligible 409 (object detail)', new ApiError('The server could not complete this request.', { path: '/x', status: 409 }), REQUEST_FAILURE_COPY.none_requestable],
    ['the approver 409', new ApiError('Approvers decide directly; open the review instead.', { path: '/x', status: 409 }), REQUEST_FAILURE_COPY.approver],
    ['the request-key 409', new ApiError('request_key already belongs to a different approval request', { path: '/x', status: 409 }), REQUEST_FAILURE_COPY.key_conflict],
  ];

  it.each(classified)('keeps the form and the text on %s, with copy that claims only what was recorded', async (_name, error, copy) => {
    vi.spyOn(approvalRequestsApi, 'create').mockRejectedValue(error);
    await render();
    await type('Rate-sensitive refinance candidates.');
    await submit();
    expect(failure()).toBe(copy);
    expect(field().value).toBe('Rate-sensitive refinance candidates.');
    expect(onRequested).not.toHaveBeenCalled();
    expect(getToasts()).toEqual([]);
  });

  it('says "recorded" only for the zero-eligible 409 and the governed 422', () => {
    const recorded = Object.entries(REQUEST_FAILURE_COPY).filter(([, copy]) => /\brecorded\b/.test(copy) && !/nothing was recorded/i.test(copy));
    expect(recorded.map(([kind]) => kind).sort()).toEqual(['governed', 'none_requestable']);
  });

  it('refetches the session after the approver 409 (can_approve went stale)', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    vi.spyOn(approvalRequestsApi, 'create').mockRejectedValue(
      new ApiError('Approvers decide directly; open the review instead.', { path: '/x', status: 409 }),
    );
    await render();
    await type('Reason.');
    await submit();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['session', 'access'] });
  });

  it('never says the refusal was recorded on a 503 (the refusal row may not exist: C4)', { timeout: 30_000 }, async () => {
    vi.spyOn(approvalRequestsApi, 'create').mockRejectedValue(
      new ApiError('Lakebase is temporarily unavailable.', { path: '/x', status: 503, dependency: 'lakebase' }),
    );
    await render();
    await type('Reason.');
    await submit();
    await vi.waitFor(() => expect(failure()).not.toBeNull(), { timeout: 20_000 });
    expect(failure()).not.toMatch(/recorded/i);
  });

  it('Escape and Cancel close it without sending', async () => {
    const create = vi.spyOn(approvalRequestsApi, 'create');
    await render();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(create).not.toHaveBeenCalled();
  });

  it('summarizes skips by reason', () => {
    expect(skippedSummary([])).toBeNull();
    expect(skippedSummary([
      { borrower_id: 'a', reason: 'already_requested' },
      { borrower_id: 'b', reason: 'not_contactable' },
    ])).toBe('2 skipped: 1 already requested, 1 not contactable.');
  });
});
