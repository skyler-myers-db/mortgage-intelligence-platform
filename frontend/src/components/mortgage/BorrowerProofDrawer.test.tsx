/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BorrowerProofDrawer } from './BorrowerProofDrawer';
import type { BorrowerProof } from '../../types';

const apiMocks = vi.hoisted(() => ({
  borrowerProof: vi.fn(),
}));

vi.mock('../../lib/api', () => ({
  api: {
    borrowerProof: apiMocks.borrowerProof,
  },
}));

const PROOF: BorrowerProof = {
  borrower_id: 'B-TEST',
  trusted: true,
  known_data_gaps: [],
  generated_from: 'mip.gold.borrower_dossier + mip.gold.lead_scores',
  source_refresh_at: 'dossier 2026-05-20T10:00:00Z / lead_scores 2026-05-20T10:00:00Z',
  opportunity_score: 88,
  signal_strength: 85,
  signal_strength_note: 'Signal strength is deterministic.',
  evidence_confidence_note: 'Evidence confidence is source-row confidence.',
  score_formula: {
    label: 'Opportunity score',
    expression: '0.35*98 + 0.30*95 + 0.15*70 + 0.10*80 + 0.10*90',
    result: '88 displayed opportunity score (recomputed 88)',
    source: 'mip.gold.fn_lead_score',
  },
  signal_strength_formula: {
    label: 'Signal strength',
    expression: '(98 + 95 + 70 + 80 + 90) / 5',
    result: '85% displayed signal strength',
    source: 'mip.gold.lead_scores',
  },
  rate_spread_formula: {
    label: 'Rate spread',
    expression: '(10.270% current rate - 6.360% market rate) * 100',
    result: '391 bps',
    source: 'mip.gold.fn_rate_spread',
  },
  equity_formula: {
    label: 'Equity',
    expression: '168,163 AVM - 15,000 current lien',
    result: '153,163 equity (91%)',
    source: 'mip.gold.borrower_dossier',
  },
  ltv_formula: {
    label: 'LTV',
    expression: '15,000 current lien / 168,163 AVM',
    result: '9% LTV',
    source: 'mip.gold.borrower_dossier',
  },
  score_components: [
    {
      key: 'economic_incentive',
      label: 'Economic incentive',
      value: 98,
      weight: 0.35,
      weighted_points: 34.3,
      explanation: 'Rate spread and equity.',
      source_fields: ['rate_spread_bps', 'equity_pct'],
      fair_lending_note: null,
    },
  ],
  offer_code: 'refi_plus_heloc',
  offer_label: 'Refinance + HELOC',
  offer_branches: [
    {
      code: 'refi_plus_heloc',
      label: 'Refinance + HELOC',
      passed: true,
      selected: true,
      reason: 'Rate spread and equity thresholds passed.',
    },
  ],
  evidence_rows: [
    {
      evidence_id: 'EV-1',
      source_product: 'Market Rates',
      signal_type: 'rate_spread',
      signal_value: '+391 bps',
      display_text: 'Current lien rate is 391 bps vs. par.',
      confidence: 0.92,
      timestamp: '2026-05-20T10:00:00Z',
    },
  ],
  source_assets: [
    'mip.gold.borrower_dossier',
    'mip.gold.lead_scores',
    'mip.gold.evidence_events',
    'mip.gold.fn_lead_score',
  ],
  reproduce: [
    {
      title: 'Score components',
      sql: (
        'WITH borrower AS (SELECT borrower_id, clip, opportunity_score AS dossier_opportunity_score ' +
        'FROM mip.gold.borrower_dossier WHERE borrower_id = :borrower_id) ' +
        'SELECT mip.gold.fn_lead_score(98, 95, 70, 80, 90) AS recomputed_opportunity_score'
      ),
      sql_hash: 'scorehash1234567',
      note: 'Recomputes the displayed score.',
      databricks_sql_url: null,
    },
    {
      title: 'Evidence rows',
      sql: (
        'SELECT evidence_id, source_product, signal_type, signal_value, display_text, confidence, `timestamp` ' +
        'FROM mip.gold.evidence_events WHERE evidence_id = :evidence_id'
      ),
      sql_hash: 'evhash123456789',
      note: 'Returns the same redacted evidence row shape used by the app drawer.',
      databricks_sql_url: null,
    },
  ],
};

function buttonByText(text: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error(`Button not found: ${text}`);
  }
  return button;
}

function proofFocusables(): HTMLElement[] {
  const drawer = document.querySelector('.proof-drawer');
  if (!(drawer instanceof HTMLElement)) {
    throw new Error('Proof drawer not found');
  }
  return Array.from(
    drawer.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('BorrowerProofDrawer', () => {
  let root: Root;
  let queryClient: QueryClient;
  const onClose = vi.fn();
  const writeText = vi.fn(async () => undefined);

  beforeEach(() => {
    document.body.innerHTML = '<button id="outside-before">Outside before</button><div id="root"></div><button id="outside-after">Outside after</button>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.borrowerProof.mockResolvedValue(PROOF);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    onClose.mockClear();
    writeText.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    vi.clearAllMocks();
    document.body.innerHTML = '';
  });

  async function render(open: boolean): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <BorrowerProofDrawer borrowerId="B-TEST" open={open} onClose={onClose} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  it('fetches only when opened and supports the proof tab workflow', async () => {
    await render(false);

    expect(apiMocks.borrowerProof).not.toHaveBeenCalled();

    await render(true);
    await settle();

    expect(apiMocks.borrowerProof).toHaveBeenCalledTimes(1);
    expect(apiMocks.borrowerProof).toHaveBeenCalledWith('B-TEST', expect.any(AbortSignal));
    expect(document.body.textContent).toContain('Opportunity score');
    expect(document.body.textContent).toContain('88 displayed opportunity score');

    await act(async () => {
      buttonByText('Evidence').click();
    });

    expect(document.body.textContent).toContain('0.920 evidence confidence');
    expect(document.body.textContent).not.toContain('0.920 conf.');

    await act(async () => {
      buttonByText('Lineage').click();
    });

    expect(document.body.textContent).toContain('mip.gold.borrower_dossier');
    expect(document.body.textContent).toContain('Raw identifiers and street addresses stay out of this view');

    await act(async () => {
      buttonByText('Reproduce').click();
    });

    expect(document.body.textContent).toContain('mip.gold.fn_lead_score');
    expect(document.body.textContent).toContain('recomputed_opportunity_score');
    expect(document.body.textContent).toContain('masked borrower id');
    expect(document.body.textContent).toContain('governed, masked data');
    expect(document.body.textContent).not.toContain('source_table');

    await act(async () => {
      buttonByText('Copy SQL').click();
    });

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('mip.gold.fn_lead_score'));

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('traps tab focus inside the modal drawer', async () => {
    await render(true);
    await settle();

    const focusables = proofFocusables();
    expect(focusables.length).toBeGreaterThan(1);

    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    last.focus();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });

    expect(document.activeElement).toBe(first);

    first.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true }));
    });

    expect(document.activeElement).toBe(last);

    const outside = document.getElementById('outside-after');
    if (!(outside instanceof HTMLElement)) {
      throw new Error('Outside focus target not found');
    }
    outside.focus();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });

    expect(document.activeElement).toBe(first);
  });

  it('surfaces known proof gaps without exposing source-table paths', async () => {
    apiMocks.borrowerProof.mockResolvedValueOnce({
      ...PROOF,
      trusted: false,
      known_data_gaps: [
        'Recomputed primary offer does not match the borrower dossier displayed offer.',
      ],
    });

    await render(true);
    await settle();

    expect(document.body.textContent).toContain('Proof has gaps');
    expect(document.body.textContent).toContain('Recomputed primary offer');
    expect(document.body.textContent).not.toContain('source_table');
  });
});
