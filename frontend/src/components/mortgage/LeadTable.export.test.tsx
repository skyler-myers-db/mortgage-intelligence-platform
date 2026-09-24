/**
 * @vitest-environment happy-dom
 *
 * CSV export scope and honest counts (audit tables-08, S slice, 2026-09-21).
 *
 * `exportCsv` serialised the raw `leads` prop: not the sorted rows, not the
 * selection. Its label counted `leads.length` although the eligibility gate
 * in LeadTable.csv.ts drops suppressed rows — so it could announce
 * "500 leads" and write zero. Pinned on the rendered table, reading the bytes
 * that actually reach the download:
 *
 * 1. the button and the confirmation carry the POST-eligibility row count;
 * 2. an all-suppressed scope disables the export instead of writing nothing;
 * 3. the file follows the on-screen sort;
 * 4. a selection, when one exists, is what gets exported.
 *
 * Wave 1a added the LEAD_EXPORT receipt the download waits for; here the
 * receipt resolves immediately so these tests keep pinning scope and counts.
 * LeadTable.exportReceipt.test.tsx pins the receipt ordering itself.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LeadTable } from './LeadTable';
import { describeLeadCsvExport, planLeadCsvExport } from './LeadTable.csv';
import { LEAD_EXPORT_NOTICE_MS, RULES_VERSION_TIMEOUT_MS } from './useLeadCsvExport';
import type { LeadExportContext } from './LeadTable.types';
import type { LeadSummary } from '../../types';

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
    actorEmail: null,
    sessionStatus: 'ready',
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    leadExportReceipt: vi.fn(async () => ({
      audit_event_id: 'evt-receipt-0001',
      event_type: 'LEAD_EXPORT',
      actor: 'approver@summit-mortgage.example',
      scope: 'loaded',
      row_count: 0,
      csv_sha256: 'a'.repeat(64),
      borrower_ids_sha256: 'b'.repeat(64),
      filter_fingerprint: 'c'.repeat(64),
      recorded_at: '2026-09-21T00:00:00.000Z',
    })),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

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
const DNC = 'B-AAAAAAAAAAAA4';

// Rank order A, B, SUPPRESSED, DNC. Equity desc among exportable rows: B, A.
const MIXED = [
  lead(A, 100_000),
  lead(B, 900_000),
  lead(SUPPRESSED, 400_000, { marketing_eligible: false }),
  lead(DNC, 300_000, { dnc: true }),
];

describe('planLeadCsvExport', () => {
  it('plans the loaded rows in the given order and counts the gate exclusions', () => {
    const plan = planLeadCsvExport(MIXED, new Set());
    expect(plan.scope).toBe('loaded_rows');
    expect(plan.rows.map((row) => row.borrower_id)).toEqual([A, B]);
    expect(plan.excluded).toBe(2);
  });

  it('prefers the selection and keeps the on-screen order, not the click order', () => {
    const plan = planLeadCsvExport(MIXED, new Set([B, A, SUPPRESSED]));
    expect(plan.scope).toBe('selected_rows');
    expect(plan.rows.map((row) => row.borrower_id)).toEqual([A, B]);
    expect(plan.excluded).toBe(1);
  });

  it('ignores a stale selection that is no longer in the loaded rows', () => {
    const plan = planLeadCsvExport(MIXED, new Set(['B-ZZZZZZZZZZZZ9']));
    expect(plan.scope).toBe('loaded_rows');
    expect(plan.rows).toHaveLength(2);
  });

  it('describes the export with the real count, scope, order and exclusions', () => {
    expect(describeLeadCsvExport(planLeadCsvExport(MIXED, new Set()), 'rank')).toBe(
      'Exported 2 leads in rank order. 2 excluded by the marketing-eligibility gate.',
    );
    expect(describeLeadCsvExport(planLeadCsvExport(MIXED, new Set([A])), 'equity desc')).toBe(
      'Exported 1 selected lead sorted by equity desc.',
    );
  });
});

describe('LeadTable CSV export', () => {
  let container: HTMLDivElement;
  let root: Root;
  let blobs: Blob[];
  // An export still hashing when its test ends (a timeout under load) would
  // download into the NEXT test's blobs. Every export hashes through
  // crypto.subtle.digest first, so track those digests and drain them before
  // the test is torn down (as LeadTable.exportReceipt.test.tsx does).
  const pendingDigests = new Set<Promise<ArrayBuffer>>();

  beforeEach(() => {
    blobs = [];
    const subtle = globalThis.crypto.subtle;
    const digest = subtle.digest.bind(subtle);
    vi.spyOn(subtle, 'digest').mockImplementation((algorithm, data) => {
      const pending = digest(algorithm, data);
      pendingDigests.add(pending);
      const settled = () => {
        pendingDigests.delete(pending);
      };
      pending.then(settled, settled);
      return pending;
    });
    // Capture the bytes handed to the download; never navigate.
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

  afterEach(async () => {
    vi.useRealTimers();
    // Let a late export finish (post its receipt, download) inside this test.
    await act(async () => {
      await Promise.allSettled([...pendingDigests]);
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  function mount(leads: LeadSummary[], context: LeadExportContext = {}) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={leads} exportContext={{ generatedAt: '2026-09-21T00:00:00.000Z', ...context }} />
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

  async function exportedCsv(): Promise<string> {
    await act(async () => {
      exportButton().click();
    });
    // The download waits for the receipt (hashing + POST are async).
    await vi.waitFor(() => expect(blobs).toHaveLength(1));
    return blobs[0].text();
  }

  /** borrower_id column of the data rows (metadata + header stripped). */
  function dataRowIds(csv: string): string[] {
    const lines = csv.split('\n').filter((line) => !line.startsWith('#'));
    return lines.slice(1).map((line) => line.split(',')[0]);
  }

  it('labels the button with the post-eligibility row count, not leads.length', () => {
    mount(MIXED);

    expect(exportButton().textContent).toContain('Export 2 leads');
    expect(exportButton().getAttribute('aria-label')).toBe('Export 2 leads as CSV');
    expect(exportButton().disabled).toBe(false);
  });

  it('disables the export, with the reason, when the gate would write zero rows', async () => {
    mount([lead(SUPPRESSED, 400_000, { marketing_eligible: false }), lead(DNC, 300_000, { dnc: true })]);

    expect(exportButton().disabled).toBe(true);
    expect(exportButton().getAttribute('aria-label')).toBe('Export 0 leads as CSV');
    expect(exportButton().getAttribute('title')).toContain('marketing-eligibility gate');

    await act(async () => {
      exportButton().click();
    });
    expect(blobs).toHaveLength(0);
    expect(container.querySelector('[data-testid="lead-export-notice"]')).toBeNull();
  });

  it('confirms the export with the same count the file holds', async () => {
    mount(MIXED);
    const csv = await exportedCsv();

    expect(dataRowIds(csv)).toEqual([A, B]);
    expect(csv).toContain('# exported_rows=2');
    expect(csv).toContain('# export_scope=loaded_rows');
    expect(csv).toContain('# row_order=rank');
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="lead-export-notice"]')?.textContent).toBe(
        'Exported 2 leads in rank order. 2 excluded by the marketing-eligibility gate.',
      );
    });
  });

  it('retires the confirmation strip after 8 seconds and keeps the audit receipt line', async () => {
    // Only timeouts are faked: hashing, the receipt and React's scheduler run as usual.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    mount(MIXED);
    // The whole export settles inside one act scope, so the commit that shows
    // the strip AND the effect that arms its 8 s timer have both run when act
    // returns. Outside act, React may flush that effect in a later scheduler
    // task than the commit: a check that saw the strip could then advance the
    // clock before any timer existed (always so on a cold first render).
    await act(async () => {
      exportButton().click();
      await vi.waitFor(() => expect(blobs).toHaveLength(1));
    });
    const notice = () => container.querySelector('[data-testid="lead-export-notice"]');
    const receiptLine = () => container.querySelector('[data-testid="lead-export-receipt"]');
    expect(notice()).not.toBeNull();
    expect(receiptLine()?.textContent).toBe('Exported 2 rows · audit evt-receipt-0001');

    act(() => {
      vi.advanceTimersByTime(LEAD_EXPORT_NOTICE_MS - 1);
    });
    expect(notice(), 'the strip stays until 8 s have passed').not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(1);
    });

    expect(notice()).toBeNull();
    expect(receiptLine()?.textContent).toBe('Exported 2 rows · audit evt-receipt-0001');
  });

  it('writes the rows in the on-screen sort order', async () => {
    mount(MIXED);
    const sortEquity = container.querySelector<HTMLButtonElement>('button[aria-label="Sort by Equity"]');
    if (!sortEquity) throw new Error('equity sort header not rendered');
    act(() => sortEquity.click());

    const csv = await exportedCsv();

    expect(dataRowIds(csv)).toEqual([B, A]);
    expect(csv).toContain('# row_order=equity desc');
  });

  it('exports the selection when one exists', async () => {
    mount(MIXED);
    const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${B}"]`);
    if (!checkbox) throw new Error('lead checkbox not rendered');
    act(() => checkbox.click());

    expect(exportButton().textContent).toContain('Export 1 selected');
    expect(exportButton().getAttribute('aria-label')).toBe('Export 1 selected as CSV');

    const csv = await exportedCsv();

    expect(dataRowIds(csv)).toEqual([B]);
    expect(csv).toContain('# export_scope=selected_rows');
    expect(csv).toContain('# exported_rows=1');
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="lead-export-notice"]')?.textContent).toBe(
        'Exported 1 selected lead in rank order.',
      );
    });
  });

  /**
   * Export provenance with no mount-time reads (audit delivery-08). The
   * refresh time rides on /api/leads (X-Data-Refreshed-At); the rules
   * version is resolved on the click, BEFORE the bytes are built and hashed,
   * bounded to 4 s, and any failure stamps 'unknown' without blocking.
   */
  describe('provenance stamps', () => {
    it('stamps the refresh time and the rules version resolved on the click', async () => {
      const resolveRulesVersion = vi.fn(async () => 'rules.itm_2026_09');
      mount(MIXED, { refreshedAt: '2026-09-21T07:30:00Z', resolveRulesVersion });
      expect(resolveRulesVersion).not.toHaveBeenCalled();

      const csv = await exportedCsv();

      expect(resolveRulesVersion).toHaveBeenCalledTimes(1);
      expect(csv).toContain('# refreshed_at=2026-09-21T07:30:00Z');
      expect(csv).toContain('# rules_version=rules.itm_2026_09');
    });

    it('waits for the rules version before it hashes anything', async () => {
      let answer: (version: string) => void = () => undefined;
      const pending = new Promise<string>((resolve) => {
        answer = resolve;
      });
      mount(MIXED, { resolveRulesVersion: () => pending });
      await act(async () => {
        exportButton().click();
      });
      expect(globalThis.crypto.subtle.digest).not.toHaveBeenCalled();

      await act(async () => {
        answer('rules.late');
      });
      await vi.waitFor(() => expect(blobs).toHaveLength(1));
      expect(await blobs[0].text()).toContain('# rules_version=rules.late');
    });

    it('stamps unknown and still downloads when the rules read fails', async () => {
      mount(MIXED, { resolveRulesVersion: () => Promise.reject(new Error('503')) });

      const csv = await exportedCsv();

      expect(csv).toContain('# rules_version=unknown');
      await vi.waitFor(() => {
        expect(container.querySelector('[data-testid="lead-export-receipt"]')?.textContent).toContain('evt-receipt-0001');
      });
    });

    it('stamps unknown when the rules read outlives its 4 s bound', async () => {
      const bound = new AbortController();
      const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(bound.signal);
      mount(MIXED, { resolveRulesVersion: () => new Promise<string>(() => undefined) });
      await act(async () => {
        exportButton().click();
      });
      expect(timeout).toHaveBeenCalledWith(RULES_VERSION_TIMEOUT_MS);
      expect(blobs).toHaveLength(0);

      await act(async () => {
        bound.abort();
      });
      await vi.waitFor(() => expect(blobs).toHaveLength(1));
      expect(await blobs[0].text()).toContain('# rules_version=unknown');
    });

    it('refuses to export while the rows on screen belong to the previous filters', async () => {
      const resolveRulesVersion = vi.fn(async () => 'rules.itm_2026_09');
      const reason = 'Export waits for the rows of the current filters';
      mount(MIXED, { exportBlockedReason: reason, resolveRulesVersion });

      expect(exportButton().disabled).toBe(false);
      expect(exportButton().getAttribute('aria-disabled')).toBe('true');
      expect(exportButton().getAttribute('title')).toBe(reason);
      await act(async () => {
        exportButton().click();
      });
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });

      expect(resolveRulesVersion).not.toHaveBeenCalled();
      expect(blobs).toHaveLength(0);
      expect(container.querySelector('[data-testid="lead-export-receipt"]')).toBeNull();
    });
  });
});
