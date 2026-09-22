/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecisionReceipt as DecisionReceiptPayload } from '../../lib/apiTypes';

const apiMocks = vi.hoisted(() => ({
  auditReceipt: vi.fn(),
}));

vi.mock('../../lib/api', () => {
  class ApiError extends Error {
    status: number | null;

    // Same signature as the real ApiError (lib/apiTransport.ts) so the
    // call sites below type-check against the module they import.
    constructor(message: string, opts: { path: string; status?: number | null } = { path: '' }) {
      super(message);
      this.status = opts.status ?? 500;
    }
  }
  return { api: apiMocks, ApiError };
});

const appMocks = vi.hoisted(() => ({
  canAccessAdmin: true,
  setDrawer: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    canAccessAdmin: appMocks.canAccessAdmin,
    setDrawer: appMocks.setDrawer,
    showEvidence: true,
    showConfidence: true,
  }),
}));

import { ApiError } from '../../lib/api';
import { DecisionReceipt, auditExplorerHref } from './DecisionReceipt';
import { PRINT_HOST_CLASS } from './DecisionReceipt.actions';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const AUDIT_ID = '5b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d';

const LEDGER: DecisionReceiptPayload = {
  audit_event_id: AUDIT_ID,
  event_type: 'APPROVE',
  decision: 'approved',
  approval_id: '11111111-1111-4111-8111-111111111111',
  borrower_id: 'B-0000000000001',
  offer_code: 'refi_plus_heloc',
  offer_label: 'Refinance + home-equity review',
  campaign_id: '7e373ef5-d4b6-4fea-b555-0cb925987a72',
  variant_name: 'Supervisor B',
  channel: 'email',
  rationale_code: null,
  copy_generation_id: '22222222-2222-4222-8222-222222222222',
  copy_hash: 'c'.repeat(64),
  approver: 'ledger.approver@summit.example',
  request_id: '33333333-3333-4333-8333-333333333333',
  correlation_id: 'corr-ledger-0001',
  created_at: '2026-07-13T12:05:00Z',
  evidence_ids: ['ev-1', 'ev-2'],
  evidence_assets: ['mip.gold.fn_next_best_offer', 'mip.gold.fn_rate_spread', 'mip.gold.fn_lead_score'],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('DecisionReceipt', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    appMocks.canAccessAdmin = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.querySelectorAll(`.${PRINT_HOST_CLASS}`).forEach((node) => node.remove());
    delete document.documentElement.dataset.print;
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

  const receipt = () => container.querySelector<HTMLElement>('[data-testid="decision-receipt"]');
  const field = (key: string) => container.querySelector<HTMLElement>(`[data-receipt-field="${key}"]`)?.textContent;

  it('renders the recording skeleton until the ledger read-back resolves, then only ledger values', async () => {
    const pending = deferred<DecisionReceiptPayload>();
    apiMocks.auditReceipt.mockReturnValue(pending.promise);
    mount({ reveal: true, score: { opportunityScore: 91, confidence: 94 } });
    await settle();

    expect(apiMocks.auditReceipt).toHaveBeenCalledWith(AUDIT_ID, expect.anything());
    const skeleton = container.querySelector<HTMLElement>('[data-testid="decision-receipt-pending"]')!;
    expect(skeleton.getAttribute('aria-busy')).toBe('true');
    expect(skeleton.textContent).toContain('Recording decision…');
    expect(receipt()).toBeNull();
    expect(container.textContent).not.toContain(AUDIT_ID);

    await act(async () => pending.resolve(LEDGER));
    await settle();
    const card = receipt()!;
    expect(container.querySelector('[data-testid="decision-receipt-pending"]')).toBeNull();
    expect(card.dataset.auditEventId).toBe(AUDIT_ID);
    expect(card.classList.contains('decision-receipt--approved')).toBe(true);
    expect(card.classList.contains('decision-receipt--reveal')).toBe(true);
    expect(field('audit')).toBe(AUDIT_ID);
    expect(field('borrower')).toBe('B-0000000000001');
    expect(field('offer')).toBe('Refinance + home-equity review');
    expect(field('channel')).toBe('Email');
    expect(field('campaign')).toBe('7e373ef5-d4b · Supervisor B');
    expect(field('copy-hash')).toBe('c'.repeat(64));
    expect(field('copy-generation')).toBe('22222222-2222-4222-8222-222222222222');
    expect(field('approver')).toBe('ledger.approver@summit.example');
    expect(field('request')).toBe('33333333-3333-4333-8333-333333333333');
    expect(field('correlation')).toBe('corr-ledger-0001');
    expect(field('approval')).toBe('11111111-1111-4111-8111-111111111111');
    expect(field('recorded')).toMatch(/2026/);
    // One EvidenceChip per cited Unity Catalog asset, plus the bound evidence ids.
    expect(card.querySelectorAll('[data-testid="decision-receipt-evidence"] .evidence-chip')).toHaveLength(3);
    expect(card.textContent).toContain('evidence ev-1 · ev-2');
    expect(card.querySelector('[data-testid="decision-receipt-score"]')?.textContent).toContain('91');
    const link = card.querySelector<HTMLAnchorElement>('[data-testid="decision-receipt-explorer-link"]')!;
    expect(link.getAttribute('href')).toBe(auditExplorerHref(AUDIT_ID));
    expect(link.getAttribute('href')).toContain(`audit_event_id=${AUDIT_ID}`);
  });

  it('renders the finished receipt without the reveal for a durable decision and hides the explorer link from non-admins', async () => {
    appMocks.canAccessAdmin = false;
    apiMocks.auditReceipt.mockResolvedValue({ ...LEDGER, decision: 'rejected', rationale_code: 'do_not_call' });
    mount();
    await settle();

    const card = receipt()!;
    expect(card.classList.contains('decision-receipt--reveal')).toBe(false);
    expect(card.classList.contains('decision-receipt--rejected')).toBe(true);
    expect(field('reason')).toBe('Do Not Call');
    expect(card.querySelector('[data-testid="decision-receipt-explorer-link"]')).toBeNull();
    expect(card.querySelector('[data-testid="decision-receipt-score"]')).toBeNull();
  });

  it('shows a neutral "Recorded; receipt unavailable" state with the audit id on a scoped 403', async () => {
    apiMocks.auditReceipt.mockRejectedValue(new ApiError('forbidden', { path: '/api/v1/audit/receipt', status: 403 }));
    mount();
    await settle();

    expect(receipt()).toBeNull();
    const unavailable = container.querySelector<HTMLElement>('[data-testid="decision-receipt-unavailable"]')!;
    expect(unavailable.textContent).toContain('Recorded; receipt unavailable');
    expect(unavailable.querySelector('[data-testid="decision-receipt-audit-id"]')?.textContent).toContain(AUDIT_ID);
    expect([...unavailable.querySelectorAll('button')].map((button) => button.textContent?.trim())).toEqual(['Copy audit id']);
  });

  it('offers a retry for a non-scoped read-back failure', async () => {
    apiMocks.auditReceipt.mockRejectedValueOnce(new ApiError('lakebase unavailable', { path: '/api/v1/audit/receipt', status: 503 })).mockResolvedValue(LEDGER);
    mount();
    await settle();

    const unavailable = container.querySelector<HTMLElement>('[data-testid="decision-receipt-unavailable"]')!;
    expect(unavailable.textContent).toContain("Couldn't read the ledger row back: lakebase unavailable");
    const retry = [...unavailable.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Retry read-back')!;
    await act(async () => retry.click());
    await settle();
    expect(receipt()).not.toBeNull();
  });

  it('copies the audit id and prints the receipt only', async () => {
    apiMocks.auditReceipt.mockResolvedValue(LEDGER);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const print = vi.fn();
    Object.defineProperty(window, 'print', { value: print, configurable: true });
    mount();
    await settle();
    const card = receipt()!;
    const buttons = () => [...card.querySelectorAll<HTMLButtonElement>('button')];

    await act(async () => buttons().find((button) => button.textContent?.trim() === 'Copy audit id')!.click());
    await settle();
    expect(writeText).toHaveBeenCalledWith(AUDIT_ID);
    expect(buttons().some((button) => button.textContent?.trim() === 'Copied audit id')).toBe(true);

    act(() => buttons().find((button) => button.textContent?.trim() === 'Print receipt')!.click());
    expect(print).toHaveBeenCalledTimes(1);
    expect(document.documentElement.dataset.print).toBe('decision-receipt');
    const host = document.querySelector<HTMLElement>(`.${PRINT_HOST_CLASS}`)!;
    expect(host.querySelector('[data-testid="decision-receipt"]')?.textContent).toContain(AUDIT_ID);
    act(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(document.querySelector(`.${PRINT_HOST_CLASS}`)).toBeNull();
    expect(document.documentElement.dataset.print).toBeUndefined();
  });
});
