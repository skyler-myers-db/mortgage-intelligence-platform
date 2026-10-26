/**
 * @vitest-environment happy-dom
 *
 * Column visibility on the RENDERED table (tables-05 step 2, W5e), with the
 * route's own column derivation (lead-queue.columns.ts) feeding the hidden
 * set through LeadTableHiddenColumnsContext:
 *  - a hidden column leaves the colgroup, the header and every row in the
 *    same order, so no header sits over the wrong cell (the row reads the
 *    same visible set as the header, LeadTableRow.tsx);
 *  - the route's HIDE control renders through LeadTableHeaderSlotContext,
 *    right after the VIEW pill;
 *  - the context value is interned on the hide VALUE, so an unrelated
 *    re-render with an equal `?hide=` re-renders no row's Status cell (a
 *    renderCost-style counting wrapper), while a real change does;
 *  - no hidden set drops a compliance caveat: with the view's host cell
 *    (Status in Review, Relationship in Sales ops) hidden, every DNC /
 *    Suppressed / Owner-unresolved chip renders in the Borrower cell.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';

const statusCellRenders = vi.hoisted(() => [] as string[]);
const appValues = vi.hoisted(() => ({
  approvals: {},
  setApproval: () => undefined,
  setLastBorrowerId: () => undefined,
  openConsoleRecentActivity: () => undefined,
  saveLead: () => undefined,
  isLeadSaved: () => false,
  setDrawer: () => undefined,
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    ...appValues,
    drawer: null,
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: 'comfortable',
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

vi.mock('./LeadTableWorkflowCells', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./LeadTableWorkflowCells')>();
  function CountedStatusCell(props: Parameters<typeof actual.LeadStatusCell>[0]) {
    'use no memo';

    statusCellRenders.push(props.lead.borrower_id);
    return <actual.LeadStatusCell {...props} />;
  }
  return { ...actual, LeadStatusCell: CountedStatusCell };
});

import { LeadTable } from './LeadTable';
import { LeadTableHeaderSlotContext, LeadTableHiddenColumnsContext, type LeadTableView } from './LeadTable.columns';
import { useLeadQueueColumnState } from '../../routes/lead-queue.columns';

const IDS = ['B-HIDECOLUMNS01', 'B-HIDECOLUMNS02', 'B-HIDECOLUMNS03'];
const LEADS = IDS.map((borrowerId, index) => ({
  borrower_id: borrowerId,
  clip: `clip_${borrowerId}`,
  display_name: `Owner ${borrowerId}`,
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  segment_codes: ['itm'],
  equity_estimate: 250000 + index * 1000,
  rate_spread_bps: 120,
  opportunity_score: 88 - index,
  confidence: 80,
  recommended_offer_code: 'refi',
  recommended_offer: 'Refinance',
  why_now: 'test',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
})) as unknown as LeadSummary[];
/** One row per compliance caveat, plus a row that carries two (and the plain LEADS[0]). */
const FLAGGED = [
  { ...LEADS[0], borrower_id: 'B-HIDEFLAGGED01', dnc: true, marketing_eligible: false },
  { ...LEADS[1], borrower_id: 'B-HIDEFLAGGED02', marketing_eligible: false, suppression_reason: 'opt_out' },
  { ...LEADS[2], borrower_id: 'B-HIDEFLAGGED03', has_unresolved_owner: true, marketing_eligible: false },
  LEADS[0],
] as unknown as LeadSummary[];
const FLAG_LABELS: Record<string, string[]> = {
  'B-HIDEFLAGGED01': ['DNC'],
  'B-HIDEFLAGGED02': ['Suppressed'],
  'B-HIDEFLAGGED03': ['Owner unresolved', 'Suppressed'],
  [IDS[0]!]: [],
};
const noop = (_view: LeadTableView) => undefined;

let setTick: ((value: number) => void) | null = null;

/** The route's derivation: a NEW URLSearchParams every render, as useSearchParams hands out. */
function Harness({ search, leads = LEADS }: { search: string; leads?: LeadSummary[] }) {
  'use no memo';

  const [tick, setTickState] = useState(0);
  useEffect(() => {
    setTick = setTickState;
  }, []);
  const state = useLeadQueueColumnState(new URLSearchParams(search));
  return (
    <div data-tick={tick}>
      <LeadTableHiddenColumnsContext.Provider value={state.hidden}>
        <LeadTableHeaderSlotContext.Provider value={<span data-testid="hide-slot">HIDE</span>}>
          <LeadTable leads={leads} view={state.view} onViewChange={noop} />
        </LeadTableHeaderSlotContext.Provider>
      </LeadTableHiddenColumnsContext.Provider>
    </div>
  );
}

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function render(search: string, leads?: LeadSummary[]) {
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/lead-queue']}>
          <Harness search={search} leads={leads} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  installLocalStorage();
  statusCellRenders.length = 0;
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  setTick = null;
});

const headerLabels = () => [...container.querySelectorAll('thead tr:first-child th')].map((th) => th.textContent?.trim() ?? '');
const rows = () => [...container.querySelectorAll<HTMLTableRowElement>('tbody tr[data-borrower-row]')];

describe('hidden columns on the rendered table', () => {
  it('drops Segments and Rate from the colgroup, the header and every row, in the same order', () => {
    render('hide=segments,rate');
    const table = container.querySelector('table')!;
    expect(table.className).toBe(
      'tbl lead-table__table lead-table__table--default lead-table__table--hiding '
      + 'lead-table__table--hide-segments lead-table__table--hide-rate',
    );
    const cols = [...table.querySelectorAll('colgroup col')].map((col) => col.className);
    expect(cols).not.toContain('lead-table__col-segments');
    expect(cols).not.toContain('lead-table__col-rate');
    expect(cols).toHaveLength(10);
    const labels = headerLabels();
    expect(labels).toHaveLength(10);
    expect(labels).not.toContain('Segments');
    expect(labels.some((label) => label.startsWith('Rate'))).toBe(false);
    expect(rows()).toHaveLength(IDS.length);
    for (const row of rows()) {
      expect(row.querySelectorAll(':scope > td'), row.dataset.borrowerRow).toHaveLength(labels.length);
    }
    // The evidence, confidence and approval columns always render.
    expect(labels).toEqual(expect.arrayContaining(['Primary offer', 'Score', 'Signal', 'Approval']));
  });

  it('renders the full view with no hidden set (Segment Intelligence provides none)', () => {
    render('');
    expect(container.querySelector('table')!.className).toBe('tbl lead-table__table lead-table__table--default');
    expect(headerLabels()).toHaveLength(12);
    for (const row of rows()) expect(row.querySelectorAll(':scope > td')).toHaveLength(12);
  });

  it('renders the route HIDE slot right after the VIEW pill', () => {
    render('');
    const actions = container.querySelector('.lead-table__header-actions')!;
    const view = actions.querySelector('[aria-label^="VIEW:"]')!.closest('.filter-root')!;
    // In the header's `.lead-table__hide-slot` wrapper (display: contents), the compact actions' hook.
    expect(view.nextElementSibling?.className).toBe('lead-table__hide-slot');
    expect(view.nextElementSibling?.firstElementChild?.getAttribute('data-testid')).toBe('hide-slot');
  });

  it('an unrelated re-render with an equal ?hide= re-renders no Status cell; a hide change does', () => {
    render('hide=segments,rate');
    expect(new Set(statusCellRenders), 'precondition: every row rendered its Status cell').toEqual(new Set(IDS));
    statusCellRenders.length = 0;
    act(() => setTick?.(1));
    act(() => setTick?.(2));
    expect(container.querySelector('[data-tick]')?.getAttribute('data-tick'), 'precondition: the harness re-rendered').toBe('2');
    expect(statusCellRenders).toEqual([]);

    render('hide=segments');
    expect(new Set(statusCellRenders), 'non-vacuity: a real hide change re-renders the rows').toEqual(new Set(IDS));
  });
});

describe('compliance caveats under any hidden set', () => {
  const flagsIn = (row: Element) => [...row.querySelectorAll('.lead-table__flag')].map((chip) => chip.textContent?.trim() ?? '');

  it.each([
    ['', 'status', false],
    ['hide=status', null, true],
    ['hide=location,segments,equity,rate,status', null, true],
    ['view=sales-ops', 'relationship', false],
    ['view=sales-ops&hide=relationship', null, true],
    ['view=sales-ops&hide=location,relationship,assigned,outreach,last-touch,segments,equity,rate', null, true],
  ] as const)('?%s: every DNC / Suppressed / Owner-unresolved row keeps its chips, once', (search, host, inBorrower) => {
    render(search, FLAGGED);
    const rendered = rows();
    expect(rendered.map((row) => row.dataset.borrowerRow).sort()).toEqual(Object.keys(FLAG_LABELS).sort());
    const labels = headerLabels();
    expect(labels.includes('Status') || labels.includes('Relationship'), 'precondition: the host header matches the case').toBe(host !== null);
    for (const row of rendered) {
      const id = row.dataset.borrowerRow!;
      expect(flagsIn(row), id).toEqual(FLAG_LABELS[id]);
      const borrowerCell = row.querySelector(':scope > td.is-primary')!;
      expect(flagsIn(borrowerCell), `${id}: caveats in the Borrower cell`).toEqual(inBorrower ? FLAG_LABELS[id] : []);
    }
  });
});
