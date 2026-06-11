/**
 * @vitest-environment happy-dom
 *
 * aria-sort contract for the lead table (re-audit 2026-06-11: the
 * remediation wired `aria-sort` onto the <th> columnheaders — the only
 * role the ARIA spec defines it for — but shipped no committed
 * assertion; the claim was browser-probe only). This renders the real
 * LeadTable and pins:
 *
 * 1. every sortable header carries aria-sort (7 columns);
 * 2. the idle state is "none" everywhere (default sort is rank, which
 *    is not one of the sortable headers);
 * 3. activating a header flips it to descending, then ascending, while
 *    every other header stays "none".
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LeadTable } from './LeadTable';
import type { LeadSummary } from '../../types';

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {},
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

function lead(borrowerId: string, score: number): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: score,
    confidence: 80,
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

describe('LeadTable aria-sort columnheaders', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={[lead('B-AAAAAAAAAAAA1', 90), lead('B-AAAAAAAAAAAA2', 70)]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it('exposes aria-sort on every sortable header, idle state none', () => {
    mount();
    const headers = Array.from(container.querySelectorAll('th[aria-sort]'));
    expect(headers).toHaveLength(7);
    expect(headers.map((th) => th.getAttribute('aria-sort'))).toEqual(
      Array(7).fill('none'),
    );
  });

  it('activating Score flips its header through descending then ascending', () => {
    mount();
    const scoreButton = Array.from(
      container.querySelectorAll('th[aria-sort] button'),
    ).find((btn) => btn.getAttribute('aria-label') === 'Sort by Score') as HTMLButtonElement;
    expect(scoreButton).toBeTruthy();

    act(() => scoreButton.click());
    const scoreTh = () => scoreButton.closest('th');
    expect(scoreTh()?.getAttribute('aria-sort')).toBe('descending');

    act(() => scoreButton.click());
    expect(scoreTh()?.getAttribute('aria-sort')).toBe('ascending');

    const others = Array.from(container.querySelectorAll('th[aria-sort]')).filter(
      (th) => th !== scoreTh(),
    );
    expect(others.map((th) => th.getAttribute('aria-sort'))).toEqual(
      Array(6).fill('none'),
    );
  });
});
