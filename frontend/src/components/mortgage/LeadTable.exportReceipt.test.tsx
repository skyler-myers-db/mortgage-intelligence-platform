/**
 * @vitest-environment happy-dom
 *
 * The audited export (audit tables-08, wave 1a), pinned on the rendered table
 * and the bytes that reach the download:
 *
 * 1. one click = exactly one POST /leads/export-receipt whose declaration
 *    names the scope, the post-eligibility row count, the borrower ids in
 *    file order, a 64-hex SHA-256 of the CSV bytes and one of the id list;
 * 2. the download does NOT start until the receipt resolves, and then the
 *    audit id is shown next to the export button;
 * 3. a refused receipt (422) means no download and a visible error;
 * 4. the digests match what the file and the id list actually hash to.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LeadTable } from './LeadTable';
import { sha256Hex, type LeadExportReceipt, type LeadExportReceiptRequest } from '../../lib/apiClients/leadExport';
import type { LeadSummary } from '../../types';

// vi.mock factories are hoisted above every other statement, so the state
// they close over must be hoisted with them.
const mocks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) {
      super(message);
      this.status = status;
    }
  }
  const receiptCalls: LeadExportReceiptRequest[] = [];
  const receipt: { impl: (declaration: LeadExportReceiptRequest) => Promise<LeadExportReceipt> } = {
    impl: () => Promise.reject(new Error('receipt implementation not set')),
  };
  return { FakeApiError, receiptCalls, receipt };
});
const { FakeApiError, receiptCalls } = mocks;

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    canAccessAdmin: true,
    actorEmail: null,
    sessionStatus: 'ready',
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    leadExportReceipt: (declaration: LeadExportReceiptRequest) => {
      mocks.receiptCalls.push(declaration);
      return mocks.receipt.impl(declaration);
    },
  },
  ApiError: mocks.FakeApiError,
  isAbortError: () => false,
}));

function receiptFor(declaration: LeadExportReceiptRequest): LeadExportReceipt {
  return {
    audit_event_id: 'evt-receipt-4242',
    event_type: 'LEAD_EXPORT',
    actor: 'approver@summit-mortgage.example',
    scope: declaration.scope,
    row_count: declaration.row_count,
    csv_sha256: declaration.csv_sha256,
    borrower_ids_sha256: declaration.borrower_ids_sha256,
    filter_fingerprint: 'c'.repeat(64),
    recorded_at: '2026-09-21T00:00:00.000Z',
  };
}

function lead(borrowerId: string, equity: number, overrides: Partial<LeadSummary> = {}): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: equity,
    rate_spread_bps: 120,
    opportunity_score: 80,
    confidence: 80,
    recommended_offer: 'Refinance',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    marketing_eligible: true,
    consent_status: 'opt_in',
    dnc: false,
    ...overrides,
  } as unknown as LeadSummary;
}

const A = 'B-AAAAAAAAAAAA1';
const B = 'B-AAAAAAAAAAAA2';
const SUPPRESSED = 'B-AAAAAAAAAAAA3';
const ROWS = [lead(A, 100_000), lead(B, 900_000), lead(SUPPRESSED, 400_000, { marketing_eligible: false })];
const SHA256_HEX = /^[0-9a-f]{64}$/;

describe('LeadTable audited export', () => {
  let container: HTMLDivElement;
  let root: Root;
  let blobs: Blob[];

  beforeEach(() => {
    blobs = [];
    receiptCalls.length = 0;
    mocks.receipt.impl = async (declaration) => receiptFor(declaration);
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob: Blob | MediaSource) => {
      blobs.push(blob as Blob);
      return 'blob:lead-export';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  function mount(leads: LeadSummary[]) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable
              leads={leads}
              exportContext={{
                generatedAt: '2026-09-21T00:00:00.000Z',
                filters: 'states=IL&segment=itm',
              }}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  const exportButton = () => {
    const button = container.querySelector<HTMLButtonElement>('[data-testid="lead-export"]');
    if (!button) throw new Error('export button not rendered');
    return button;
  };

  async function clickExport() {
    await act(async () => {
      exportButton().click();
    });
  }

  it('asks for exactly one receipt describing the selection before anything downloads', async () => {
    mount(ROWS);
    const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${B}"]`);
    if (!checkbox) throw new Error('lead checkbox not rendered');
    act(() => checkbox.click());
    const checkboxA = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${A}"]`);
    if (!checkboxA) throw new Error('lead checkbox not rendered');
    act(() => checkboxA.click());

    await clickExport();
    await vi.waitFor(() => expect(blobs).toHaveLength(1));

    expect(receiptCalls).toHaveLength(1);
    const declaration = receiptCalls[0];
    expect(declaration.scope).toBe('selected');
    expect(declaration.row_count).toBe(2);
    expect(declaration.borrower_ids).toEqual([A, B]);
    expect(declaration.csv_sha256).toMatch(SHA256_HEX);
    expect(declaration.borrower_ids_sha256).toMatch(SHA256_HEX);
    expect(declaration.filters).toEqual({ states: 'IL', segment: 'itm' });

    // The digests describe the real file and the real id list.
    expect(declaration.csv_sha256).toBe(await sha256Hex(await blobs[0].text()));
    expect(declaration.borrower_ids_sha256).toBe(await sha256Hex(JSON.stringify([A, B])));
  });

  it('does not start the download until the receipt resolves, then shows the audit id', async () => {
    let resolveReceipt: (receipt: LeadExportReceipt) => void = () => undefined;
    mocks.receipt.impl = (declaration) =>
      new Promise<LeadExportReceipt>((resolve) => {
        resolveReceipt = () => resolve(receiptFor(declaration));
      });
    mount(ROWS);

    await clickExport();
    await vi.waitFor(() => expect(receiptCalls).toHaveLength(1));

    // Receipt in flight: nothing downloaded, the button says so. It is
    // aria-disabled, not natively disabled, so keyboard focus stays on it.
    expect(blobs).toHaveLength(0);
    expect(exportButton().getAttribute('aria-disabled')).toBe('true');
    expect(exportButton().disabled).toBe(false);
    expect(exportButton().textContent).toContain('Recording export');
    expect(container.querySelector('[data-testid="lead-export-receipt"]')).toBeNull();

    await act(async () => {
      resolveReceipt(receiptFor(receiptCalls[0]));
    });
    await vi.waitFor(() => expect(blobs).toHaveLength(1));

    const receipt = container.querySelector('[data-testid="lead-export-receipt"]');
    expect(receipt?.textContent).toBe('Exported 2 rows · audit evt-receipt-4242');
    const link = receipt?.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/admin-config?audit_event_id=evt-receipt-4242#audit');
    expect(exportButton().disabled).toBe(false);
    expect(exportButton().hasAttribute('aria-disabled')).toBe(false);
    expect(exportButton().textContent).toContain('Export 2 leads');
  });

  it('refuses to download when the receipt is refused, and says so', async () => {
    mocks.receipt.impl = async () => {
      throw new FakeApiError('export declaration does not match the borrower id list', 422);
    };
    mount(ROWS);

    await clickExport();
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="lead-export-error"]')).not.toBeNull();
    });

    expect(blobs).toHaveLength(0);
    expect(receiptCalls).toHaveLength(1);
    const error = container.querySelector('[data-testid="lead-export-error"]');
    expect(error?.getAttribute('role')).toBe('alert');
    expect(error?.textContent).toBe(
      'Export refused: the audit receipt did not match the file. Nothing was downloaded.',
    );
    expect(container.querySelector('[data-testid="lead-export-receipt"]')).toBeNull();
    expect(container.querySelector('[data-testid="lead-export-notice"]')).toBeNull();
  });

  it('tells a refused request apart from a receipt that did not match the file', async () => {
    // A 422 from the schema or the audit store's metadata policy is not the
    // digest check: the copy must not claim the file and receipt disagreed.
    mocks.receipt.impl = async () => {
      throw new FakeApiError('audit metadata value is not allowed', 422);
    };
    mount(ROWS);

    await clickExport();
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="lead-export-error"]')).not.toBeNull();
    });

    expect(blobs).toHaveLength(0);
    expect(container.querySelector('[data-testid="lead-export-error"]')?.textContent).toBe(
      'Export refused: the audit ledger would not record this export. Nothing was downloaded.',
    );
  });

  it('ignores a second click while the first receipt is in flight', async () => {
    let resolveReceipt: (receipt: LeadExportReceipt) => void = () => undefined;
    mocks.receipt.impl = (declaration) =>
      new Promise<LeadExportReceipt>((resolve) => {
        resolveReceipt = () => resolve(receiptFor(declaration));
      });
    mount(ROWS);

    await clickExport();
    await vi.waitFor(() => expect(receiptCalls).toHaveLength(1));
    await clickExport();
    await act(async () => {
      resolveReceipt(receiptFor(receiptCalls[0]));
    });
    await vi.waitFor(() => expect(blobs).toHaveLength(1));

    expect(receiptCalls).toHaveLength(1);
  });
});
