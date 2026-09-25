/**
 * @vitest-environment happy-dom
 *
 * Wave 3 additions to the Decision receipt (lane w3-score-anatomy):
 *  - the cache-only recompute seal in "Score at decision" (wow-stage-3
 *    remainder): shown only from a cached, trusted, gap-free proof whose
 *    score is the one on the receipt; never a /proof read;
 *  - focusHeading (carryover #11): focus moves to the current heading on
 *    mount and on each pending -> read-back / unavailable swap, only from
 *    <body> or from inside the receipt;
 *  - the routing line (carryover #12): approvals only, read-back and
 *    unavailable cards, captioned as the approve response's;
 *  - explorerLink={false} for the explorer's own receipt.
 *
 * Mutation checks (lane report): letting the receipt read the proof
 * (`useBorrowerProof(…, false)` -> `true`) fails "a cold cache keeps the lead
 * payload note and reads no proof"; removing the focus effect fails "moves
 * focus to the pending heading, then to the read-back heading".
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecisionReceipt as DecisionReceiptPayload } from '../../lib/apiTypes';
import { queryKeys } from '../../lib/queryKeys';
import { formatDate } from '../../lib/time';
import { sampleProof } from '../../mocks/scoreAnatomyProof';

const apiMocks = vi.hoisted(() => ({
  auditReceipt: vi.fn(),
  borrowerProof: vi.fn(),
}));

vi.mock('../../lib/api', () => {
  class ApiError extends Error {
    status: number | null;

    constructor(message: string, opts: { path: string; status?: number | null } = { path: '' }) {
      super(message);
      this.status = opts.status ?? null;
    }
  }
  return { api: apiMocks, ApiError };
});

vi.mock('../AppContext', () => ({
  useApp: () => ({ canAccessAdmin: true, setDrawer: vi.fn(), showEvidence: true, showConfidence: true }),
}));

import { ApiError } from '../../lib/api';
import { DecisionReceipt } from './DecisionReceipt';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const AUDIT_ID = '5b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d';
const BORROWER_ID = 'B-0000000000001';

function ledger(decision: 'approved' | 'rejected' = 'approved'): DecisionReceiptPayload {
  return {
    audit_event_id: AUDIT_ID,
    event_type: decision === 'approved' ? 'APPROVE' : 'OUTREACH_REJECT',
    decision,
    approval_id: '11111111-1111-4111-8111-111111111111',
    borrower_id: BORROWER_ID,
    offer_code: 'refi_plus_heloc',
    offer_label: 'Refinance + home-equity review',
    campaign_id: null,
    variant_name: null,
    channel: 'email',
    rationale_code: decision === 'rejected' ? 'low_intent' : null,
    copy_generation_id: null,
    copy_hash: null,
    approver: 'ledger.approver@summit.example',
    request_id: '33333333-3333-4333-8333-333333333333',
    correlation_id: 'corr-ledger-0001',
    created_at: '2026-07-13T12:05:00Z',
    evidence_ids: ['ev-1'],
    evidence_assets: ['mip.gold.fn_next_best_offer'],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const ROUTING = { assignedTo: 'lo.a@summit-mortgage.example', followUpAt: '2026-07-19T15:00:00Z' };

describe('DecisionReceipt wave-3 additions', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.querySelectorAll('[data-testid="outside-control"]').forEach((node) => node.remove());
  });

  function mount(props: Partial<Parameters<typeof DecisionReceipt>[0]> = {}) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <DecisionReceipt auditEventId={AUDIT_ID} {...props} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  async function settle() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }

  const SCORE = { opportunityScore: 90, confidence: 87 };
  const seal = () => container.querySelector('[data-testid="decision-receipt-seal"]');
  const note = () => container.querySelector('[data-testid="decision-receipt-score-note"]');

  describe('the cache-only seal', () => {
    it('a cold cache keeps the lead payload note and reads no proof', async () => {
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount({ score: SCORE });
      await settle();
      expect(note()?.textContent).toBe('from the lead payload');
      expect(seal()).toBeNull();
      expect(apiMocks.borrowerProof).not.toHaveBeenCalled();
    });

    it('a cached trusted, gap-free proof with the same score seals it', async () => {
      queryClient.setQueryData(queryKeys.borrowerProof(BORROWER_ID), sampleProof({ borrower_id: BORROWER_ID, opportunity_score: 90 }));
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount({ score: SCORE });
      await settle();
      expect(seal()?.textContent).toBe(SCORE_ANATOMY_COPY.seal);
      expect(note()).toBeNull();
      expect(apiMocks.borrowerProof).not.toHaveBeenCalled();
    });

    it.each([
      ['untrusted', { trusted: false, known_data_gaps: ['A gap.'] }],
      ['trusted with a gap', { known_data_gaps: ['A gap.'] }],
      ['a different score', { opportunity_score: 89 }],
    ])('a cached proof that is %s keeps the note', async (_label, overrides) => {
      queryClient.setQueryData(
        queryKeys.borrowerProof(BORROWER_ID),
        sampleProof({ borrower_id: BORROWER_ID, opportunity_score: 90, ...overrides }),
      );
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount({ score: SCORE });
      await settle();
      expect(seal()).toBeNull();
      expect(note()?.textContent).toBe('from the lead payload');
    });

    it('without a score line there is no score section, sealed or not', async () => {
      queryClient.setQueryData(queryKeys.borrowerProof(BORROWER_ID), sampleProof({ borrower_id: BORROWER_ID, opportunity_score: 90 }));
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount();
      await settle();
      expect(container.querySelector('[data-testid="decision-receipt-score"]')).toBeNull();
      expect(seal()).toBeNull();
    });
  });

  describe('focusHeading', () => {
    it('moves focus to the pending heading, then to the read-back heading', async () => {
      const pending = deferred<DecisionReceiptPayload>();
      apiMocks.auditReceipt.mockReturnValue(pending.promise);
      (document.activeElement as HTMLElement | null)?.blur();
      mount({ decidedHere: true, focusHeading: true });
      await settle();
      const pendingHeading = container.querySelector('[data-testid="decision-receipt-pending"] .h-4');
      expect(document.activeElement).toBe(pendingHeading);
      expect(pendingHeading?.getAttribute('tabindex')).toBe('-1');

      await act(async () => pending.resolve(ledger()));
      await settle();
      const heading = container.querySelector('[data-testid="decision-receipt"] .h-4');
      expect(heading?.textContent).toBe('Decision receipt');
      expect(document.activeElement).toBe(heading);
    });

    it('moves focus to an unavailable card heading too', async () => {
      apiMocks.auditReceipt.mockRejectedValue(new ApiError('forbidden', { path: '/x', status: 403 }));
      (document.activeElement as HTMLElement | null)?.blur();
      mount({ decidedHere: true, decision: 'rejected', focusHeading: true });
      await settle();
      const heading = container.querySelector('[data-testid="decision-receipt-unavailable"] .h-4');
      expect(document.activeElement).toBe(heading);
    });

    it('never takes focus from a control the reviewer moved to', async () => {
      const outside = document.createElement('button');
      outside.dataset.testid = 'outside-control';
      document.body.appendChild(outside);
      outside.focus();
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount({ decidedHere: true, focusHeading: true });
      await settle();
      expect(document.activeElement).toBe(outside);
    });

    it('a passive receipt (no focusHeading) never moves focus', async () => {
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      (document.activeElement as HTMLElement | null)?.blur();
      mount();
      await settle();
      expect(document.activeElement).toBe(document.body);
      expect(container.querySelector('[data-testid="decision-receipt"] .h-4')?.hasAttribute('tabindex')).toBe(false);
    });
  });

  describe('the routing line', () => {
    const line = () => container.querySelector('[data-testid="decision-receipt-routing"]');
    const expected = `Routing: Assigned to ${ROUTING.assignedTo} · follow-up ${formatDate('2026-07-19T15:00:00Z', { withYear: false })} (from the approval response)`;

    it('reads on an approval read-back, captioned as the approve response', async () => {
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount({ decision: 'approved', routing: ROUTING });
      await settle();
      expect(line()?.textContent).toBe(expected);
      expect(line()?.classList.contains('decision-receipt__routing')).toBe(true);
      // The old in-page routing chip stays retired (feedback-guard pins it at 0).
      expect(container.querySelector('[data-testid="routing-confirm"], .outreach-routing__confirm')).toBeNull();
    });

    it('stays on an unavailable approval card', async () => {
      apiMocks.auditReceipt.mockRejectedValue(new ApiError('forbidden', { path: '/x', status: 403 }));
      mount({ decision: 'approved', routing: ROUTING });
      await settle();
      expect(container.querySelector('[data-testid="decision-receipt-unavailable"]')).not.toBeNull();
      expect(line()?.textContent).toBe(expected);
    });

    it('is absent while pending, for a rejection, and when the approval was not routed', async () => {
      const pending = deferred<DecisionReceiptPayload>();
      apiMocks.auditReceipt.mockReturnValue(pending.promise);
      mount({ decision: 'approved', routing: ROUTING });
      await settle();
      expect(line()).toBeNull();
      await act(async () => pending.resolve(ledger('rejected')));
      await settle();
      expect(line()).toBeNull();

      queryClient.clear();
      apiMocks.auditReceipt.mockResolvedValue(ledger());
      mount({ decision: 'approved', routing: { assignedTo: null, followUpAt: null } });
      await settle();
      expect(line()).toBeNull();
    });
  });

  it('explorerLink={false} drops "Open in audit explorer"; the default keeps it', async () => {
    apiMocks.auditReceipt.mockResolvedValue(ledger());
    mount({ compact: true, explorerLink: false });
    await settle();
    expect(container.querySelector('[data-testid="decision-receipt"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="decision-receipt-explorer-link"]')).toBeNull();

    mount({ compact: true });
    await settle();
    expect(container.querySelector('[data-testid="decision-receipt-explorer-link"]')).not.toBeNull();
  });
});
