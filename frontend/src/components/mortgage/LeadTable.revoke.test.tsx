/**
 * @vitest-environment happy-dom
 *
 * Revoke from the Lead Queue's Approved row (flow-v2), on the rendered
 * LeadTable:
 *  - the role gate: only an approver sees Revoke, named for the borrower,
 *    with the design-system tooltip and no single-key binding;
 *  - the outreach gate: none or queued only;
 *  - the campaign gate: a bound queue hides it (its provenance row says why);
 *  - pessimistic: the row stays Approved while the revoke is on the wire and
 *    reads pending (Approve / Reject back) only after the 200, through
 *    applyRevocations, with no active refetch of an audited read.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';
import { clearToasts } from '../../lib/toast';

const session = vi.hoisted(() => ({ canApprove: true }));
const revokeApproval = vi.hoisted(() => vi.fn());
const campaign = vi.hoisted(() => vi.fn());

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: () => undefined,
    setLastBorrowerId: () => undefined,
    openConsoleRecentActivity: () => undefined,
    setDrawer: () => undefined,
    showEvidence: true,
    showConfidence: true,
    canApprove: session.canApprove,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: 'comfortable',
  }),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: vi.fn(),
    approve: vi.fn(),
    campaign,
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve([]),
    borrowerLifecycle: (borrowerId: string) => Promise.resolve({
      borrower_id: borrowerId, approval_status: 'approved', outreach_status: 'none', approval_id: APPROVAL,
    }),
  },
}));
vi.mock('../../lib/apiClients/outreachRevoke', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/apiClients/outreachRevoke')>()),
  revokeApproval,
}));

import { LeadTable } from './LeadTable';
import { REVOKE_APPROVAL_EVENT } from './LeadTable.constants';

const APPROVAL = '6f1c4d2e-8b3a-4c5d-9e6f-7a8b9c0d1e2f';
const APPROVED = 'B-0000000000RQ1';
const SENT = 'B-0000000000RQ2';
const PENDING = 'B-0000000000RQ3';

function lead(borrowerId: string, approval: string, outreach: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 90,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: approval,
    outreach_status: outreach,
  } as unknown as LeadSummary;
}

const LEADS = [lead(APPROVED, 'approved', 'queued'), lead(SENT, 'approved', 'sent'), lead(PENDING, 'pending', 'none')];

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('LeadTable: Revoke from the Approved row', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(path = '/lead-queue') {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[path]}>
            <LeadTable leads={LEADS} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await flush();
  }

  const revokeButton = (id: string) => container.querySelector<HTMLButtonElement>(`[data-testid="lead-revoke-${id}"]`);
  const cell = (id: string) => container.querySelector(`[data-testid="lead-approval-cell-${id}"]`);

  beforeAll(async () => {
    await import('./RevokeApprovalPanel');
  }, 60_000);

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    clearToasts();
    session.canApprove = true;
    revokeApproval.mockReset();
    campaign.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('offers an approver Revoke on an Approved row whose outreach is queued, named for the borrower, with no key binding', async () => {
    await render();
    const button = revokeButton(APPROVED);
    expect(button).not.toBeNull();
    expect(button!.getAttribute('aria-label')).toBe(`Revoke approval for ${APPROVED}`);
    expect(button!.hasAttribute('aria-keyshortcuts')).toBe(false);
    expect(button!.hasAttribute('data-rum-target')).toBe(false);
    expect(button!.hasAttribute('title')).toBe(false);
    const tip = button!.getAttribute('data-tooltip');
    expect(tip && document.getElementById(tip)?.textContent).toBe('Revoke approval');
    expect(cell(APPROVED)?.querySelector('.chip--success')?.textContent).toContain('Approved');
  });

  it('offers nothing once outreach has started, nor on a pending row', async () => {
    await render();
    expect(revokeButton(SENT)).toBeNull();
    expect(revokeButton(PENDING)).toBeNull();
  });

  it('offers nothing to a non-approver', async () => {
    session.canApprove = false;
    await render();
    expect(revokeButton(APPROVED)).toBeNull();
  });

  it('offers nothing on a campaign-bound queue', async () => {
    campaign.mockResolvedValue({ campaign_id: 'cmp-1', message_variants: [{ variant_name: 'A', channel: 'email' }] });
    await render('/lead-queue?campaign_id=cmp-1&variant_name=A');
    expect(container.querySelector('[data-testid="campaign-operational-provenance"]')?.textContent)
      .toContain("Campaign-bound approvals can't be revoked in this release.");
    expect(revokeButton(APPROVED)).toBeNull();
  });

  it("opens on the queue receipt's request with its approval id, reading no lifecycle", async () => {
    const lifecycle = vi.spyOn((await import('../../lib/api')).api, 'borrowerLifecycle');
    revokeApproval.mockResolvedValue({
      revoked: true, approval_id: 'rv-2', revoked_approval_id: 'apr-receipt-1', audit_event_id: 'audit-rv-2', released_assignment_id: null,
    });
    await render();
    act(() => {
      window.dispatchEvent(new CustomEvent(REVOKE_APPROVAL_EVENT, { detail: { borrowerId: APPROVED, approvalId: 'apr-receipt-1' } }));
    });
    await vi.waitFor(async () => {
      await flush();
      expect(document.querySelector('[data-testid="revoke-approval-reason"]')).not.toBeNull();
    }, { timeout: 10_000 });
    const field = document.querySelector<HTMLTextAreaElement>('[data-testid="revoke-approval-reason"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Mis-keyed offer.');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => document.querySelector<HTMLButtonElement>('[data-testid="revoke-approval-confirm"]')!.click());
    await flush();
    expect((revokeApproval.mock.calls[0] as [Record<string, string>])[0]).toMatchObject({ borrower_id: APPROVED, approval_id: 'apr-receipt-1' });
    expect(lifecycle).not.toHaveBeenCalled();
    lifecycle.mockRestore();
  }, 20_000);

  it('keeps the row Approved while the revoke is on the wire, then reads it pending after the 200', async () => {
    let resolve!: (value: unknown) => void;
    revokeApproval.mockImplementation(() => new Promise((settle) => {
      resolve = settle;
    }));
    await render();
    act(() => revokeButton(APPROVED)!.click());
    await vi.waitFor(async () => {
      await flush();
      expect(document.querySelector('[data-testid="revoke-approval-reason"]'), document.querySelector('[data-testid="revoke-approval-panel"]')?.textContent ?? 'no panel').not.toBeNull();
    }, { timeout: 10_000 });
    const field = document.querySelector<HTMLTextAreaElement>('[data-testid="revoke-approval-reason"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Mis-keyed offer.');
      field!.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => document.querySelector<HTMLButtonElement>('[data-testid="revoke-approval-confirm"]')!.click());
    await flush();
    expect(revokeApproval).toHaveBeenCalledTimes(1);
    expect((revokeApproval.mock.calls[0] as [Record<string, string>])[0]).toMatchObject({ borrower_id: APPROVED, approval_id: APPROVAL });
    // On the wire: still Approved, Revoke shows its own loading state.
    expect(cell(APPROVED)?.textContent).toContain('Approved');
    expect(revokeButton(APPROVED)?.getAttribute('aria-busy')).toBe('true');
    expect(container.querySelector(`[data-testid="lead-approve-${APPROVED}"]`)).toBeNull();

    await act(async () => resolve({
      revoked: true, approval_id: 'rv-1', revoked_approval_id: APPROVAL, audit_event_id: 'audit-rv-1', released_assignment_id: null,
    }));
    await flush();
    expect(cell(APPROVED)?.textContent).not.toContain('Approved');
    expect(container.querySelector(`[data-testid="lead-approve-${APPROVED}"]`)).not.toBeNull();
    expect(document.querySelector('[data-testid="revoke-approval-panel"]')).toBeNull();
  }, 20_000);
});
