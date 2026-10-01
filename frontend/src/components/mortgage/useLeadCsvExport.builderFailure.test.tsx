/**
 * @vitest-environment happy-dom
 *
 * The lead CSV builder (LeadTable.csv) loads on the Export click, out of the
 * shared LeadTable chunk. A builder that cannot load (a stale deploy, a
 * dropped connection) must write nothing: no LEAD_EXPORT receipt, no
 * download, and a plain error with no asset URL on screen.
 */

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const leadExportReceipt = vi.hoisted(() => vi.fn());

vi.mock('../../lib/api', () => ({
  api: { leadExportReceipt },
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

vi.mock('./LeadTable.csv', () => {
  throw new TypeError('Failed to fetch dynamically imported module: https://app.example/assets/LeadTable.csv-x1.js');
});

import { planLeadCsvExport } from './LeadTable.csvPlan';
import { useLeadCsvExport } from './useLeadCsvExport';

const ROWS = ['B-EXPORTCHUNK01', 'B-EXPORTCHUNK02'].map((borrowerId) => ({
  borrower_id: borrowerId,
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  segment_codes: ['itm'],
  opportunity_score: 80,
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

describe('useLeadCsvExport when the CSV builder cannot load', () => {
  let container: HTMLDivElement;
  let root: Root;
  let downloads: number;

  beforeEach(() => {
    downloads = 0;
    leadExportReceipt.mockReset();
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      downloads += 1;
      return 'blob:lead-export';
    });
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

  it('posts no receipt, downloads nothing and says so without an asset URL', async () => {
    const resolveRulesVersion = vi.fn(async () => 'rules.itm_2026_09');
    await act(async () => {
      await hook!.exportCsv({
        plan: planLeadCsvExport(ROWS, new Set()),
        approvals: {},
        exportContext: { filters: 'state=IL', resolveRulesVersion },
        rowOrder: 'rank',
      });
    });

    expect(leadExportReceipt).not.toHaveBeenCalled();
    expect(resolveRulesVersion, 'nothing runs before the builder is in hand').not.toHaveBeenCalled();
    expect(downloads).toBe(0);
    expect(hook!.state).toEqual({
      status: 'error',
      message: 'Export not recorded: the export could not load; reload the page, then export again. Nothing was downloaded.',
    });
  });
});
