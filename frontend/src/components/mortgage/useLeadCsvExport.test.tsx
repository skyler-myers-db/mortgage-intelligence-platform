/**
 * @vitest-environment happy-dom
 *
 * useLeadCsvExport owns the audited export's contract, so it refuses a
 * blocked export itself (audit delivery-08). LeadTable's Export button
 * checks the same reason first, but any other caller of exportCsv (a
 * keymap or palette verb) reaches the hook directly. While keepPreviousData
 * placeholder rows are on screen, a LEAD_EXPORT declaration would pair the
 * NEW filters with the PREVIOUS cohort's ids, so the hook resolves no rules
 * version, posts no receipt and downloads nothing. A control shows that the
 * same request with no block does export, so the refusal is not vacuous.
 */

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import type { LeadExportContext } from './LeadTable.types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const leadExportReceipt = vi.hoisted(() => vi.fn());

vi.mock('../../lib/api', () => ({
  api: { leadExportReceipt },
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

import { planLeadCsvExport } from './LeadTable.csv';
import { useLeadCsvExport } from './useLeadCsvExport';

const ROWS = ['B-EXPORTHOOK001', 'B-EXPORTHOOK002'].map((borrowerId) => ({
  borrower_id: borrowerId,
  clip: `clip_${borrowerId}`,
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  segment_codes: ['itm'],
  equity_estimate: 100_000,
  rate_spread_bps: 120,
  opportunity_score: 80,
  confidence: 80,
  recommended_offer: 'Refinance',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  marketing_eligible: true,
  consent_status: 'opt_in',
  dnc: false,
}) as unknown as LeadSummary);

type Hook = ReturnType<typeof useLeadCsvExport>;
let hook: Hook | null = null;

function Harness() {
  const current = useLeadCsvExport();
  useEffect(() => {
    hook = current;
  });
  return null;
}

describe('useLeadCsvExport placeholder gate', () => {
  let container: HTMLDivElement;
  let root: Root;
  let downloads: number;

  beforeEach(() => {
    downloads = 0;
    leadExportReceipt.mockReset();
    leadExportReceipt.mockResolvedValue({
      audit_event_id: 'evt-receipt-hook',
      event_type: 'LEAD_EXPORT',
      actor: 'approver@summit-mortgage.example',
      scope: 'loaded',
      row_count: 2,
      csv_sha256: 'a'.repeat(64),
      borrower_ids_sha256: 'b'.repeat(64),
      filter_fingerprint: 'c'.repeat(64),
      recorded_at: '2026-09-21T00:00:00.000Z',
    });
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      downloads += 1;
      return 'blob:lead-export';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<Harness />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    hook = null;
    vi.restoreAllMocks();
  });

  async function exportWith(exportContext: LeadExportContext) {
    await act(async () => {
      await hook!.exportCsv({
        plan: planLeadCsvExport(ROWS, new Set()),
        approvals: {},
        exportContext,
        rowOrder: 'rank',
      });
    });
  }

  it('refuses a blocked export: no rules read, no LEAD_EXPORT receipt, no download', async () => {
    const resolveRulesVersion = vi.fn(async () => 'rules.itm_2026_09');

    await exportWith({
      filters: 'state=IL',
      resolveRulesVersion,
      exportBlockedReason: 'Export waits for the rows of the current filters',
    });

    expect(resolveRulesVersion).not.toHaveBeenCalled();
    expect(leadExportReceipt, 'no LEAD_EXPORT row for placeholder rows').not.toHaveBeenCalled();
    expect(downloads).toBe(0);
    expect(hook!.state.status).toBe('idle');
  });

  it('control: the same request with no block posts one receipt and downloads', async () => {
    const resolveRulesVersion = vi.fn(async () => 'rules.itm_2026_09');

    await exportWith({ filters: 'state=IL', resolveRulesVersion, exportBlockedReason: null });

    expect(resolveRulesVersion).toHaveBeenCalledTimes(1);
    expect(leadExportReceipt).toHaveBeenCalledTimes(1);
    expect(downloads).toBe(1);
    expect(hook!.state.status).toBe('done');
  });
});
