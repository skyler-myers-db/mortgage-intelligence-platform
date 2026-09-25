/**
 * @vitest-environment happy-dom
 *
 * Explorer receipts (wow-stage-3): an EXPANDED decision row reads its
 * Decision receipt back (compact, no explorer link pointing at itself, no
 * announcement: a passive, audit-neutral read). A collapsed row and a
 * non-decision row make no receipt request.
 *
 * Mutation check (lane report): rendering the receipt for every expanded row
 * (`receiptModule?.isDecisionReceiptEvent(event) ? … : null` ->
 * `receiptModule ? receiptModule.DecisionReceipt : null`) fails "an expanded
 * VIEW_LEADS row makes no receipt request".
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditEventRow, DecisionReceipt as DecisionReceiptPayload } from '../../lib/apiTypes';

const apiMocks = vi.hoisted(() => ({ auditReceipt: vi.fn(), borrowerProof: vi.fn() }));

vi.mock('../../lib/api', () => {
  class ApiError extends Error {
    status: number | null = null;
  }
  return { api: apiMocks, ApiError };
});

vi.mock('../AppContext', () => ({
  useApp: () => ({ canAccessAdmin: true, setDrawer: vi.fn(), showEvidence: true, showConfidence: true }),
}));

import { isDecisionReceiptEvent } from '../mortgage/DecisionReceipt.copy';
import { AuditEventTableRow } from './AdminAuditExplorer.row';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.setConfig({ testTimeout: 30_000 });

const APPROVE_ROW: AuditEventRow = {
  event_id: '6f2a9c1e-3b4d-4e5f-8a6b-7c8d9e0f1a2b',
  actor: 'ledger.approver@summit.example',
  action: 'outreach.approve',
  entity_type: 'borrower',
  entity_id: 'B-0000000000001',
  payload_json: { offer_code: 'refi' },
  evidence_ids: ['ev-1'],
  created_at: '2026-07-14T15:00:04Z',
  event_type: 'APPROVE',
  request_id: 'req-1',
  correlation_id: 'corr-1',
};

const VIEW_ROW: AuditEventRow = {
  ...APPROVE_ROW,
  event_id: 'evt-view-0001',
  action: 'VIEW_LEADS',
  event_type: 'VIEW_LEADS',
};

const RECEIPT: DecisionReceiptPayload = {
  audit_event_id: APPROVE_ROW.event_id,
  event_type: 'APPROVE',
  decision: 'approved',
  approval_id: 'apr-1',
  borrower_id: 'B-0000000000001',
  offer_code: 'refi',
  offer_label: 'Refinance review',
  campaign_id: null,
  variant_name: null,
  channel: 'email',
  rationale_code: null,
  copy_generation_id: null,
  copy_hash: null,
  approver: 'ledger.approver@summit.example',
  request_id: 'req-1',
  correlation_id: 'corr-1',
  created_at: '2026-07-14T15:00:04Z',
  evidence_ids: ['ev-1'],
  evidence_assets: ['mip.gold.fn_next_best_offer'],
};

describe('explorer decision receipts', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.auditReceipt.mockResolvedValue(RECEIPT);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
  });

  async function renderRow(event: AuditEventRow, expanded: boolean) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <table>
              <tbody>
                <AuditEventTableRow event={event} expanded={expanded} copiedValue={null} onToggle={() => undefined} onCopy={() => undefined} />
              </tbody>
            </table>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 8; i += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
  }

  it('an expanded decision row reads its receipt once: compact, no explorer link, no announcement', async () => {
    await renderRow(APPROVE_ROW, true);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="decision-receipt"]')).not.toBeNull(), { timeout: 15_000 });
    expect(apiMocks.auditReceipt).toHaveBeenCalledTimes(1);
    expect(apiMocks.auditReceipt).toHaveBeenCalledWith(APPROVE_ROW.event_id, expect.anything());
    const receipt = container.querySelector('[data-testid="decision-receipt"]');
    expect(receipt?.classList.contains('decision-receipt--compact')).toBe(true);
    expect(container.querySelector('[data-testid="decision-receipt-explorer-link"]')).toBeNull();
    expect(container.querySelector('[data-testid="decision-receipt-announcement"]')?.textContent).toBe('');
    expect(apiMocks.borrowerProof).not.toHaveBeenCalled();
  });

  it('a collapsed decision row makes no receipt request', async () => {
    await renderRow(APPROVE_ROW, false);
    expect(container.querySelector('[data-testid="audit-explorer-receipt"]')).toBeNull();
    expect(apiMocks.auditReceipt).not.toHaveBeenCalled();
  });

  it('an expanded VIEW_LEADS row makes no receipt request', async () => {
    // Load the receipt module first (a decision row), so the VIEW row below
    // renders with it in hand: only the decision check keeps it from reading.
    await renderRow(APPROVE_ROW, true);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="decision-receipt"]')).not.toBeNull(), { timeout: 15_000 });
    apiMocks.auditReceipt.mockClear();
    await renderRow(VIEW_ROW, true);
    expect(container.querySelector('[data-testid="audit-explorer-receipt"]')).toBeNull();
    expect(container.textContent).toContain('Event details');
    expect(apiMocks.auditReceipt).not.toHaveBeenCalled();
  });

  it('isDecisionReceiptEvent matches the receipt endpoint, normalized like the backend', () => {
    expect(isDecisionReceiptEvent({ event_type: ' approve ', action: 'x' })).toBe(true);
    expect(isDecisionReceiptEvent({ event_type: 'OUTREACH_HOLD' })).toBe(true);
    expect(isDecisionReceiptEvent({ event_type: null, action: ' Outreach.Reject ' })).toBe(true);
    expect(isDecisionReceiptEvent({ event_type: 'VIEW_LEADS', action: 'VIEW_LEADS' })).toBe(false);
    expect(isDecisionReceiptEvent({ event_type: 'APPROVE_OUTREACH', action: 'APPROVE_OUTREACH' })).toBe(false);
    expect(isDecisionReceiptEvent({})).toBe(false);
  });
});
