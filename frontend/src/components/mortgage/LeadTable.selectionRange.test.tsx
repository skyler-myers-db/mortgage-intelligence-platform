/**
 * @vitest-environment happy-dom
 *
 * Bulk selection on the rendered table (audit tables-07):
 *
 *  - Shift-click on a row checkbox selects from the anchor (the last plain
 *    toggle) to the clicked row, in ON-SCREEN order (after a sort), skipping
 *    rows that may not be selected; Shift+X does the same to the cursor row.
 *  - The selection the table acts on is the stored selection intersected
 *    with the rows on screen: after the rows change, the toolbar count, the
 *    header checkbox, the CSV scope and the Cmd-K counts all drop the rows
 *    that left.
 *  - A row whose decision is on the wire is not approval-eligible.
 *  - No "select all N matching" (D-approval-flow-a3, the 12.4 #9 ruling):
 *    the header selects the loaded rows only. With every loaded row selected
 *    and more matching, the scope line says bulk actions apply only to the
 *    rows shown and offers a campaign built from the filters instead.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { currentCommandSelection } from '../command/commandSelection';
import type { LeadTableCampaignHandoff } from './LeadTable.types';

const approve = vi.fn();

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
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    approve: (...args: unknown[]) => approve(...args),
    draftOutreach: () => new Promise(() => undefined),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

function lead(n: number, equity: number, status: 'pending' | 'rejected' = 'pending'): LeadSummary {
  return {
    borrower_id: `B-AAAAAAAAAAAA${n}`,
    clip: `clip_${n}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: equity,
    rate_spread_bps: 120,
    opportunity_score: 90 - n,
    confidence: 80,
    recommended_offer: 'Refinance',
    evidence_ids: ['ev-1'],
    approval_status: status,
    marketing_eligible: true,
  } as unknown as LeadSummary;
}

// Rank order 1..6; equity order (desc) 6, 5, 4, 3, 2, 1. Row 4 is rejected.
const ROWS = [
  lead(1, 100_000),
  lead(2, 200_000),
  lead(3, 300_000),
  lead(4, 400_000, 'rejected'),
  lead(5, 500_000),
  lead(6, 600_000),
];
const id = (n: number) => `B-AAAAAAAAAAAA${n}`;

describe('LeadTable range selection and the on-screen selection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(
    leads: LeadSummary[],
    extra: { totalMatching?: number | null; campaignHandoff?: LeadTableCampaignHandoff | null } = {},
  ) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={leads} {...extra} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  const checkbox = (n: number) => container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id(n)}"]`)!;
  const click = (n: number, shiftKey = false) => {
    act(() => {
      checkbox(n).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, shiftKey }));
    });
  };
  const selected = () => ROWS.map((row) => row.borrower_id).filter((borrowerId) => (
    container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${borrowerId}"]`)?.checked
  ));
  const toolbarLabel = () => container.querySelector('.bulk-actions__label')?.textContent ?? '';
  const region = () => container.querySelector<HTMLElement>('.tbl-wrap')!;
  const press = (key: string, shiftKey = false) => {
    act(() => {
      region().dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true }));
    });
  };

  it('Shift-click selects anchor to target in on-screen order and skips rows that may not be selected', () => {
    render(ROWS);
    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Sort by Equity"]')!.click());
    // On screen: 6, 5, 4 (rejected), 3, 2, 1.
    click(5);
    click(2, true);

    expect(selected()).toEqual([id(2), id(3), id(5)]);
    expect(toolbarLabel()).toBe('3 leads selected');
  });

  it('Shift+X extends from the anchor to the cursor row', () => {
    render(ROWS);
    click(2);
    region().focus();
    // Cursor: J lands on row 1, then walks to row 6 (rank order on screen).
    for (let step = 0; step < 6; step += 1) press('j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(id(6));

    press('X', true);

    expect(selected()).toEqual([id(2), id(3), id(5), id(6)]);
  });

  it('counts, the header checkbox, the CSV scope and Cmd-K read only the rows on screen', () => {
    render(ROWS);
    click(1);
    click(2);
    click(3);
    expect(toolbarLabel()).toBe('3 leads selected');

    // A filter change: rows 1 and 2 leave the screen.
    render(ROWS.slice(2));

    expect(toolbarLabel()).toBe('1 lead selected');
    const header = container.querySelector<HTMLInputElement>('[data-testid="lead-select-all"]')!;
    expect(header.checked).toBe(false);
    expect(header.indeterminate).toBe(true);
    expect(container.querySelector('[data-testid="lead-export"]')?.textContent).toBe('Export 1 selected');
    expect(currentCommandSelection()).toMatchObject({ selectedCount: 1, approveCount: 1 });
    expect(container.querySelector('[data-testid="lead-bulk-approve"]')?.textContent).toBe('Approve 1 eligible');
  });

  describe('the scope line and the campaign handoff (tables-07 / tables-02)', () => {
    const HANDOFF: LeadTableCampaignHandoff = { href: '/portfolio-builder?occupancy=All', notCarried: [] };
    const selectAll = () => act(() => container.querySelector<HTMLInputElement>('[data-testid="lead-select-all"]')!.click());
    const scope = () => container.querySelector<HTMLElement>('[data-testid="lead-bulk-scope"]');
    /** The handoff link rides the lazy bulk chunk, which selecting every loaded row starts loading. */
    async function handoffLink(): Promise<HTMLAnchorElement> {
      let link: HTMLAnchorElement | null = null;
      await vi.waitFor(async () => {
        await act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 0));
        });
        link = container.querySelector<HTMLAnchorElement>('[data-testid="lead-bulk-campaign-handoff"]');
        expect(link).not.toBeNull();
      }, { timeout: 15_000 });
      return link!;
    }

    it('shows only when every loaded row is selected and more borrowers match', async () => {
      render(ROWS, { totalMatching: 2340, campaignHandoff: HANDOFF });
      click(1);
      expect(scope(), 'a partial selection makes no claim').toBeNull();
      selectAll();
      // Row 4 is rejected and not selectable: every selectable row is selected.
      expect(scope()?.textContent).toContain(
        'All 5 loaded borrowers are selected. 2,340 match these filters; bulk actions apply only to borrowers shown here.',
      );
      const link = await handoffLink();
      expect(link.textContent).toBe('Build a campaign from these filters');
      expect(link.getAttribute('href')).toBe('/portfolio-builder?occupancy=All');
      expect(link.className).toBe('btn btn--ghost btn--sm');
      expect(container.querySelector('[data-testid="lead-bulk-not-carried"]')).toBeNull();

      // Nothing more matches than is loaded: no scope line.
      render(ROWS, { totalMatching: 6, campaignHandoff: HANDOFF });
      expect(scope()).toBeNull();
    }, 30_000);

    it('names the filters a campaign cannot carry', async () => {
      render(ROWS, { totalMatching: 2340, campaignHandoff: { href: '/portfolio-builder', notCarried: ['segments', 'ZIPs'] } });
      selectAll();
      expect((await handoffLink()).textContent).toBe('Build a campaign from the filters that carry over');
      expect(container.querySelector('[data-testid="lead-bulk-not-carried"]')?.textContent).toBe('Not carried: segments, ZIPs');
    }, 30_000);

    it('offers no "select all N matching" control anywhere (the 12.4 #9 ruling)', async () => {
      render(ROWS, { totalMatching: 2340, campaignHandoff: HANDOFF });
      selectAll();
      await handoffLink();
      const names = [...container.querySelectorAll('button, a, input, [role="button"], [role="checkbox"]')]
        .map((element) => [element.getAttribute('aria-label'), element.textContent].filter(Boolean).join(' '));
      expect(names.length).toBeGreaterThan(5);
      expect(names.filter((name) => /select all .* matching/i.test(name))).toEqual([]);
    }, 30_000);
  });

  it('no toolbar for a selection that is entirely off screen', () => {
    render(ROWS);
    click(1);
    render(ROWS.slice(2));

    expect(container.querySelector('[data-testid="lead-bulk-actions"]')).toBeNull();
    expect(currentCommandSelection()).toBeNull();
  });
});
