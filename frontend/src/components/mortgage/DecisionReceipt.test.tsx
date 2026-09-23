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
  // Same constructor shape as the real ApiError (message, { path, status }).
  class ApiError extends Error {
    status: number | null;

    constructor(message: string, opts: { path: string; status?: number | null } = { path: '' }) {
      super(message);
      this.status = opts.status ?? null;
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
const RECEIPT_PATH = `/api/v1/audit/receipt/${AUDIT_ID}`;

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
  const announcement = () => container.querySelector<HTMLElement>('[data-testid="decision-receipt-announcement"]');
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
    // One polite live region, outside the section that swaps states.
    const live = announcement()!;
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.textContent).toBe('Reading the ledger row back');

    await act(async () => pending.resolve(LEDGER));
    await settle();
    const card = receipt()!;
    // The same live node now announces the confirmed decision.
    expect(announcement()).toBe(live);
    expect(live.textContent).toBe(`Decision receipt recorded: Approved, audit event ${AUDIT_ID}`);
    expect(container.querySelectorAll('[aria-live]')).toHaveLength(1);
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

  it('plays the reveal once: onRevealed fires after the read-back and a parent flip does not cut it short', async () => {
    const pending = deferred<DecisionReceiptPayload>();
    apiMocks.auditReceipt.mockReturnValue(pending.promise);
    const onRevealed = vi.fn();
    mount({ reveal: true, onRevealed });
    await settle();
    expect(onRevealed).not.toHaveBeenCalled();

    await act(async () => pending.resolve(LEDGER));
    await settle();
    expect(onRevealed).toHaveBeenCalledTimes(1);
    expect(receipt()!.classList.contains('decision-receipt--reveal')).toBe(true);

    // The parent records "revealed" and re-renders with reveal=false: this
    // mount keeps its reveal (no class flip mid-stagger).
    mount({ reveal: false, onRevealed });
    await settle();
    expect(receipt()!.classList.contains('decision-receipt--reveal')).toBe(true);

    // A fresh mount (collapse + re-expand) renders the receipt finished.
    act(() => root.unmount());
    root = createRoot(container);
    mount({ reveal: false, onRevealed });
    await settle();
    expect(receipt()!.classList.contains('decision-receipt--reveal')).toBe(false);
    expect(announcement()!.textContent).toBe(`Decision receipt: Approved, audit event ${AUDIT_ID}`);
    expect(onRevealed).toHaveBeenCalledTimes(1);
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
    apiMocks.auditReceipt.mockRejectedValue(new ApiError('forbidden', { path: RECEIPT_PATH, status: 403 }));
    mount();
    await settle();

    expect(receipt()).toBeNull();
    const unavailable = container.querySelector<HTMLElement>('[data-testid="decision-receipt-unavailable"]')!;
    expect(unavailable.dataset.receiptState).toBe('forbidden');
    expect(announcement()!.textContent).toBe(`Recorded; receipt unavailable, audit event ${AUDIT_ID}`);
    expect(unavailable.textContent).toContain('Recorded; receipt unavailable');
    expect(unavailable.querySelector('[data-testid="decision-receipt-audit-id"]')?.textContent).toContain(AUDIT_ID);
    expect([...unavailable.querySelectorAll('button')].map((button) => button.textContent?.trim())).toEqual(['Copy audit id']);
  });

  it('does not call a 404 read-back recorded: it says the ledger found no row and keeps the retry', async () => {
    apiMocks.auditReceipt
      .mockRejectedValueOnce(new ApiError('receipt not found', { path: RECEIPT_PATH, status: 404 }))
      .mockResolvedValue(LEDGER);
    mount({ decidedHere: true });
    await settle();

    expect(receipt()).toBeNull();
    const unavailable = container.querySelector<HTMLElement>('[data-testid="decision-receipt-unavailable"]')!;
    expect(unavailable.dataset.receiptState).toBe('not-found');
    expect(announcement()!.textContent).toBe(`Ledger row not found, audit event ${AUDIT_ID}`);
    expect(unavailable.textContent).toContain(
      'The write returned this audit id, but the ledger read-back did not find a decision row for it.',
    );
    expect(unavailable.textContent).toContain('Ledger row not found');
    // Nothing on the not-found state claims the row is in the ledger.
    expect(unavailable.textContent).not.toContain('is in the audit ledger');
    expect(unavailable.textContent).not.toContain('Recorded');
    expect(unavailable.querySelector('[data-testid="decision-receipt-audit-id"]')?.textContent).toContain(AUDIT_ID);
    const buttons = [...unavailable.querySelectorAll('button')];
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['Copy audit id', 'Retry read-back']);

    await act(async () => buttons[1].click());
    await settle();
    expect(receipt()).not.toBeNull();
  });

  it.each([
    { status: 403, state: 'forbidden', decision: 'rejected' as const, label: 'Rejected' },
    { status: 404, state: 'not-found', decision: 'approved' as const, label: 'Approved' },
    { status: 503, state: 'error', decision: 'held' as const, label: 'Held' },
  ])('keeps the caller-known outcome ($label) on the card when the read-back returns $status', async ({ status, state, decision, label }) => {
    apiMocks.auditReceipt.mockRejectedValue(new ApiError('unavailable', { path: RECEIPT_PATH, status }));
    mount({ decision, decidedHere: true });
    await settle();

    expect(receipt()).toBeNull();
    const unavailable = container.querySelector<HTMLElement>('[data-testid="decision-receipt-unavailable"]')!;
    expect(unavailable.dataset.receiptState).toBe(state);
    const outcome = unavailable.querySelector<HTMLElement>('[data-testid="decision-receipt-outcome"]');
    expect(outcome?.textContent?.trim()).toBe(label);
    expect(outcome?.querySelector('.chip')?.classList.contains(decision === 'rejected' ? 'chip--danger' : decision === 'held' ? 'chip--warning' : 'chip--success')).toBe(true);
    expect(announcement()!.textContent).toMatch(new RegExp(`^${label}\\. .+, audit event ${AUDIT_ID}$`));
    expect(unavailable.querySelector('[data-testid="decision-receipt-audit-id"]')?.textContent).toContain(AUDIT_ID);
  });

  it('words a 404 on a durable decision against the decision record, not a write made here', async () => {
    apiMocks.auditReceipt.mockRejectedValue(new ApiError('receipt not found', { path: RECEIPT_PATH, status: 404 }));
    mount({ decision: 'rejected' });
    await settle();

    const unavailable = container.querySelector<HTMLElement>('[data-testid="decision-receipt-unavailable"]')!;
    expect(unavailable.dataset.receiptState).toBe('not-found');
    expect(unavailable.textContent).toContain(
      'The decision record points at this audit id, but the ledger read-back did not find a decision row for it.',
    );
    expect(unavailable.textContent).not.toContain('The write returned');
    expect(unavailable.querySelector('[data-testid="decision-receipt-outcome"]')?.textContent?.trim()).toBe('Rejected');
  });

  it('offers a retry for a non-scoped read-back failure', async () => {
    apiMocks.auditReceipt.mockRejectedValueOnce(new ApiError('lakebase unavailable', { path: RECEIPT_PATH, status: 503 })).mockResolvedValue(LEDGER);
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
    // The clone never duplicates the live card's ids (or points at them).
    const liveTitleId = card.getAttribute('aria-labelledby')!;
    expect(document.querySelectorAll(`[id="${liveTitleId}"]`)).toHaveLength(1);
    expect(host.querySelectorAll('[id], [aria-labelledby], [aria-describedby], [aria-controls]')).toHaveLength(0);
    act(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(document.querySelector(`.${PRINT_HOST_CLASS}`)).toBeNull();
    expect(document.documentElement.dataset.print).toBeUndefined();
  });
});
