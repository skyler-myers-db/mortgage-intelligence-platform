/**
 * @vitest-environment happy-dom
 *
 * OfferDecisionOutcome (carryovers #11 and #12): a decision made in this view
 * hands its receipt the focus (Approve / Confirm reject unmount with the
 * gate, so focus would otherwise fall to <body>) and, for an approval, the
 * routing line from the approve response. A durable decision from an
 * earlier session does neither.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecisionReceipt as DecisionReceiptPayload } from '../lib/apiTypes';
import type { QueueContext } from '../lib/queueContext';

const apiMocks = vi.hoisted(() => ({ auditReceipt: vi.fn(), borrowerProof: vi.fn() }));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ canAccessAdmin: true, setDrawer: vi.fn(), showEvidence: true, showConfidence: true }),
}));

vi.mock('../components/activation/ActivationLoopPanel', () => ({ ActivationLoopPanel: () => null }));

import { OfferDecisionOutcome, type OfferDecisionOutcomeProps } from './offer-orchestrator.decision';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.setConfig({ testTimeout: 30_000 });

const AUDIT_ID = '7d3e9f1a-2b4c-4d6e-8f0a-1b2c3d4e5f60';

function receipt(decision: 'approved' | 'rejected'): DecisionReceiptPayload {
  return {
    audit_event_id: AUDIT_ID,
    event_type: decision === 'approved' ? 'APPROVE' : 'OUTREACH_REJECT',
    decision,
    approval_id: 'apr-1',
    borrower_id: 'B-0000000000001',
    offer_code: 'refi',
    offer_label: 'Refinance review',
    campaign_id: null,
    variant_name: null,
    channel: 'email',
    rationale_code: decision === 'rejected' ? 'low_intent' : null,
    copy_generation_id: null,
    copy_hash: null,
    review_mode: null,
    bulk_id: null,
    approver: 'ledger.approver@summit.example',
    request_id: 'req-1',
    correlation_id: 'corr-1',
    created_at: '2026-07-14T15:00:04Z',
    evidence_ids: [],
    evidence_assets: [],
  };
}

const BASE: OfferDecisionOutcomeProps = {
  borrowerId: 'B-0000000000001',
  offerCode: 'refi',
  channel: 'email',
  effectiveApproval: 'approved',
  auditId: AUDIT_ID,
  justDecided: true,
  approvalId: 'apr-1',
  approveError: null,
  score: { opportunityScore: 90, confidence: 87 },
  routing: { assignedTo: 'lo.a@summit-mortgage.example', followUpAt: '2026-07-19T15:00:00Z' },
};

describe('OfferDecisionOutcome', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    (document.activeElement as HTMLElement | null)?.blur();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
  });

  async function render(props: Partial<OfferDecisionOutcomeProps> = {}) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <OfferDecisionOutcome {...BASE} {...props} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    // The receipt is its own lazily loaded chunk: wait for its read-back card.
    for (let i = 0; i < 300 && !container.querySelector('[data-testid="decision-receipt"]'); i += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
    }
  }

  const heading = () => container.querySelector('[data-testid="decision-receipt"] .h-4');
  const routing = () => container.querySelector('[data-testid="decision-receipt-routing"]');

  it('an approval made here takes focus on its receipt and shows where it was routed', async () => {
    apiMocks.auditReceipt.mockResolvedValue(receipt('approved'));
    await render();
    expect(document.activeElement).toBe(heading());
    expect(routing()?.textContent).toMatch(/^Routing: Assigned to lo\.a@summit-mortgage\.example · follow-up .+ \(from the approval response\)$/);
    expect(apiMocks.borrowerProof).not.toHaveBeenCalled();
  });

  it('a rejection made here takes focus too, with no routing line', async () => {
    apiMocks.auditReceipt.mockResolvedValue(receipt('rejected'));
    await render({ effectiveApproval: 'rejected', routing: null });
    expect(document.activeElement).toBe(heading());
    expect(routing()).toBeNull();
  });

  it.each([
    { decision: 'approved' as const, chip: 'Approved · governed internal queue' },
    { decision: 'rejected' as const, chip: 'Rejected' },
  ])('an empty audit id shows the plain $decision chip and reads nothing back', async ({ decision, chip }) => {
    apiMocks.auditReceipt.mockResolvedValue(receipt(decision));
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <OfferDecisionOutcome {...BASE} effectiveApproval={decision} auditId="" routing={null} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(container.querySelector('.surface .chip')?.textContent).toContain(chip);
    expect(container.querySelector('[data-testid="decision-receipt"]')).toBeNull();
    expect(container.querySelector('[data-testid="decision-receipt-pending"]')).toBeNull();
    expect(apiMocks.auditReceipt).not.toHaveBeenCalled();
  });

  describe('Next in queue (shell-04 / flow-09)', () => {
    const QUEUE: QueueContext = {
      epoch: 'e1',
      search: '?state=IL',
      label: 'IL',
      ids: ['B-0000000000001', 'B-0000000000002'],
    };
    const nextStep = () => container.querySelector('[data-testid="offer-next-in-queue"]');

    it('after a decision made here, links the next ranked offer with the queue, and leaves focus on the receipt', async () => {
      apiMocks.auditReceipt.mockResolvedValue(receipt('approved'));
      await render({ queue: QUEUE, nextId: 'B-0000000000002' });
      const link = nextStep()?.querySelector('a');
      expect(link?.textContent).toBe('Next in queue: B-0000000000002');
      expect(link?.getAttribute('href')).toBe('/offer-orchestrator/B-0000000000002');
      expect(link?.className).toBe('btn btn--sm');
      expect(document.activeElement).toBe(heading());
    });

    it('at the end of the queue, links back to the exact filtered queue', async () => {
      apiMocks.auditReceipt.mockResolvedValue(receipt('rejected'));
      await render({ effectiveApproval: 'rejected', routing: null, queue: QUEUE, nextId: null });
      const link = nextStep()?.querySelector('a');
      expect(link?.textContent).toBe('Back to lead queue');
      expect(link?.getAttribute('href')).toBe('/lead-queue?state=IL');
    });

    it('never for a durable decision, and never without a queue', async () => {
      apiMocks.auditReceipt.mockResolvedValue(receipt('approved'));
      await render({ justDecided: false, routing: null, queue: QUEUE, nextId: 'B-0000000000002' });
      expect(heading()).not.toBeNull();
      expect(nextStep()).toBeNull();
      await render({ queue: null, nextId: null });
      expect(nextStep()).toBeNull();
    });
  });

  it('a durable decision from an earlier session neither takes focus nor shows routing', async () => {
    apiMocks.auditReceipt.mockResolvedValue(receipt('approved'));
    await render({ justDecided: false, routing: null });
    expect(heading()).not.toBeNull();
    expect(document.activeElement).toBe(document.body);
    expect(routing()).toBeNull();
  });
});
