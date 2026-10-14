/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue receipt's Revoke (flow-v2): only the in-session queue
 * receipt of an approval (decidedHere AND compact, approval_id known) shows
 * 'Revoke approval', and only to an approver; a campaign-bound approval
 * shows why it cannot be revoked instead; Offer (decidedHere, not compact)
 * and the decision history (compact, not decidedHere) are unchanged. The
 * button opens the lazy panel on the receipt's approval id, with no
 * lifecycle read. critic-08: the sliced campaign row carries its full id on
 * the design-system tooltip.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecisionReceipt as DecisionReceiptPayload } from '../../lib/apiTypes';

const apiMocks = vi.hoisted(() => ({
  auditReceipt: vi.fn(),
  session: vi.fn(),
  borrowerLifecycle: vi.fn(),
}));
const app = vi.hoisted(() => ({ canApprove: true }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    canAccessAdmin: false,
    canApprove: app.canApprove,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

import { DecisionReceipt } from './DecisionReceipt';

const AUDIT_ID = '5b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d';
const APPROVAL_ID = '11111111-1111-4111-8111-111111111111';
const LEDGER: DecisionReceiptPayload = {
  audit_event_id: AUDIT_ID,
  event_type: 'APPROVE',
  decision: 'approved',
  approval_id: APPROVAL_ID,
  borrower_id: 'B-0000000000001',
  offer_code: 'refi',
  offer_label: 'Refinance',
  campaign_id: null,
  variant_name: null,
  channel: 'email',
  rationale_code: null,
  copy_generation_id: null,
  copy_hash: 'c'.repeat(64),
  review_mode: 'individual',
  bulk_id: null,
  approver: 'ledger.approver@summit.example',
  request_id: null,
  correlation_id: null,
  created_at: '2026-07-13T12:05:00Z',
  evidence_ids: [],
  evidence_assets: [],
};

describe('DecisionReceipt: Revoke on the queue receipt', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeAll(async () => {
    await import('./RevokeApprovalPanel');
  }, 60_000);

  beforeEach(() => {
    vi.clearAllMocks();
    app.canApprove = true;
    apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: true, can_read_audit: false, presenter_mode: false });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
  });

  async function show(payload: DecisionReceiptPayload, props: { decidedHere?: boolean; compact?: boolean }) {
    apiMocks.auditReceipt.mockResolvedValue(payload);
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <DecisionReceipt auditEventId={AUDIT_ID} {...props} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await vi.waitFor(() => expect(container.querySelector('[data-testid="decision-receipt"]')).not.toBeNull());
  }

  const revoke = () => container.querySelector<HTMLButtonElement>('[data-testid="decision-receipt-revoke"]');
  const bound = () => container.querySelector('[data-testid="decision-receipt-revoke-bound"]');

  it('shows Revoke approval on the queue receipt of an approval, and opens the panel on its approval id', async () => {
    await show(LEDGER, { decidedHere: true, compact: true });
    expect(revoke()?.textContent).toBe('Revoke approval');
    act(() => revoke()!.click());
    await vi.waitFor(() => expect(container.querySelector('[data-testid="revoke-approval-reason"]')).not.toBeNull());
    expect(container.querySelector('h3')?.textContent).toContain('Revoke the approval for B-0000000000001?');
    // The receipt names the approval: no lifecycle read.
    expect(apiMocks.borrowerLifecycle).not.toHaveBeenCalled();
  });

  it.each([
    ['Offer (decidedHere, not compact)', { decidedHere: true, compact: false }],
    ['the decision history (compact, not decidedHere)', { decidedHere: false, compact: true }],
  ])('offers nothing on %s', async (_label, props) => {
    await show(LEDGER, props);
    expect(revoke()).toBeNull();
    expect(bound()).toBeNull();
  });

  it('offers nothing to a non-approver, on a rejection, or without an approval id', async () => {
    app.canApprove = false;
    await show(LEDGER, { decidedHere: true, compact: true });
    expect(revoke()).toBeNull();
    app.canApprove = true;
    client.clear();
    await show({ ...LEDGER, decision: 'rejected', event_type: 'OUTREACH_REJECT' }, { decidedHere: true, compact: true });
    expect(revoke()).toBeNull();
    client.clear();
    await show({ ...LEDGER, approval_id: null }, { decidedHere: true, compact: true });
    expect(revoke()).toBeNull();
  });

  it('says a campaign-bound approval cannot be revoked instead, and carries the campaign id on a tooltip', async () => {
    await show({ ...LEDGER, campaign_id: '7e373ef5-d4b6-4fea-b555-0cb925987a72', variant_name: 'Supervisor B' }, { decidedHere: true, compact: true });
    expect(revoke()).toBeNull();
    expect(bound()?.textContent).toBe("Campaign-bound approvals can't be revoked in this release.");
    const campaign = container.querySelector('[data-receipt-field="campaign"]');
    expect(campaign?.hasAttribute('title')).toBe(false);
    const tip = campaign?.getAttribute('data-tooltip');
    expect(tip && document.getElementById(tip)?.textContent).toBe('7e373ef5-d4b6-4fea-b555-0cb925987a72');
    expect(container.querySelector('[data-receipt-field="copy-hash"]')?.hasAttribute('title')).toBe(false);
  });
});
