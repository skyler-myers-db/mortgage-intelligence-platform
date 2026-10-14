/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue table's render cost (audit runtime-04, slices 1-3;
 * responsive-09 item 3), proven at the layer where it lived: the rendered
 * LeadTable, with the real hooks and the real virtualizer.
 *
 *   - Stable virtualizer inputs: getItemKey is a virtual-core memo
 *     dependency, so an inline one rebuilt every row measurement on every
 *     render. It keeps its identity across a cursor move; estimateSize
 *     follows the density (36 compact, 44 comfortable, plus the expanded
 *     preview's estimate); overscan is 5.
 *   - The sort runs once per (rows, sort): sortValue is not called again
 *     on a re-render that changes neither.
 *   - Form state lives in the forms (slice 2): a keystroke in the reject
 *     rationale, a change of the disposition's outcome or callback time (its
 *     free-text notes are retired, D-shell-deviations-g2) or the bulk gate's
 *     shared rationale re-renders that form only. The table's renders are counted through
 *     useLeadTableFillHeight, which the shell calls once per render.
 *   - The shell's row callbacks keep one identity (slice 3; the shell itself
 *     stays 'use no memo' under the lane's budget cut 5, so this is
 *     useStableRowCallbacks plus targeted memoization, not the compiler):
 *     a row expand, or an unrelated AppContext change, re-derives no other
 *     borrower's workflow states (a counting partial mock of
 *     leadWorkflowStates, which every row's Status cell calls) and
 *     re-renders no other borrower's Status cell (a counting wrapper: the
 *     cell's onExpand closes over the row's toggle callback, so a callback
 *     with a new identity re-renders it even where the compiled cell's own
 *     leadWorkflowStates memo still hits).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary, SalesTeamMember } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';
import type { LeadTableSort } from './LeadTable.types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({
  density: 'comfortable' as 'comfortable' | 'compact',
  // An AppContext field no table input reads (the open evidence drawer).
  drawer: null as string | null,
}));
const counts = vi.hoisted(() => ({ sortValue: 0, shellRenders: 0 }));
const workflowCalls = vi.hoisted(() => [] as string[]);
const statusCellRenders = vi.hoisted(() => [] as string[]);
// Stable, like the real AppContext's value members.
const appValues = vi.hoisted(() => ({
  approvals: {},
  setApproval: () => undefined,
  setLastBorrowerId: () => undefined,
  openConsoleRecentActivity: () => undefined,
  saveLead: () => undefined,
  isLeadSaved: () => false,
  setDrawer: () => undefined,
}));
const virtualizerOptions = vi.hoisted(() => [] as Array<{
  getItemKey: (index: number) => string | number;
  estimateSize: (index: number) => number;
  overscan?: number;
  count: number;
}>);

vi.mock('../AppContext', () => ({
  useApp: () => ({
    ...appValues,
    drawer: app.drawer,
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: app.density,
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

vi.mock('@tanstack/react-virtual', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-virtual')>();
  return {
    ...actual,
    useVirtualizer: ((options: Parameters<typeof actual.useVirtualizer>[0]) => {
      virtualizerOptions.push(options as (typeof virtualizerOptions)[number]);
      return actual.useVirtualizer(options);
    }) as typeof actual.useVirtualizer,
  };
});

vi.mock('./LeadTable.logic', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./LeadTable.logic')>();
  return {
    ...actual,
    sortValue: (...args: Parameters<typeof actual.sortValue>) => {
      counts.sortValue += 1;
      return actual.sortValue(...args);
    },
  };
});

vi.mock('./LeadTable.status', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./LeadTable.status')>();
  return {
    ...actual,
    leadWorkflowStates: (...args: Parameters<typeof actual.leadWorkflowStates>) => {
      workflowCalls.push(args[0].borrower_id);
      return actual.leadWorkflowStates(...args);
    },
  };
});

vi.mock('./LeadTableWorkflowCells', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./LeadTableWorkflowCells')>();
  function CountedStatusCell(props: Parameters<typeof actual.LeadStatusCell>[0]) {
    'use no memo';

    statusCellRenders.push(props.lead.borrower_id);
    return <actual.LeadStatusCell {...props} />;
  }
  return { ...actual, LeadStatusCell: CountedStatusCell };
});

vi.mock('./useLeadTableFillHeight', () => ({
  useLeadTableFillHeight: () => {
    counts.shellRenders += 1;
  },
}));

import { LeadTable } from './LeadTable';
import { LEAD_EXPANDED_PREVIEW_ESTIMATE_PX } from './LeadTable.constants';

const IDS = ['B-RENDERCOST001', 'B-RENDERCOST002', 'B-RENDERCOST003', 'B-RENDERCOST004'];

function lead(borrowerId: string, index: number): LeadSummary {
  return {
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
  } as unknown as LeadSummary;
}

const LEADS = IDS.map(lead);
const TEAM: SalesTeamMember[] = [
  { email: 'lo.alpha@summit.example', display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: null, capacity_per_day: 25, active: true },
];

/** Type one character at a time, as a person does: one input event per keystroke. */
function typeInto(target: HTMLInputElement | HTMLTextAreaElement, text: string): void {
  const prototype = target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  for (const character of text) {
    act(() => {
      setter?.call(target, `${target.value}${character}`);
      target.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
}

function click(element: Element | null): void {
  if (!(element instanceof HTMLElement)) throw new Error('nothing to click');
  act(() => element.click());
}

let setTick: ((value: number) => void) | null = null;

/** Re-renders LeadTable on `setTick` with the same props (uncompiled, so the element is new). */
function Harness({ sort, expandedId = null }: { sort: LeadTableSort | null; expandedId?: string | null }) {
  'use no memo';

  const [tick, setTickState] = useState(0);
  useEffect(() => {
    setTick = setTickState;
  }, []);
  return (
    <div data-tick={tick}>
      <LeadTable
        leads={LEADS}
        sort={sort}
        onSortChange={() => undefined}
        expandedId={expandedId}
        onExpandedChange={() => undefined}
      />
    </div>
  );
}

describe('LeadTable render cost (runtime-04)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  function render(node: ReactNode) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>{node}</MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    app.density = 'comfortable';
    app.drawer = null;
    workflowCalls.length = 0;
    statusCellRenders.length = 0;
    counts.sortValue = 0;
    counts.shellRenders = 0;
    virtualizerOptions.length = 0;
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

  const lastOptions = () => virtualizerOptions[virtualizerOptions.length - 1];

  it('keeps getItemKey stable across a cursor move, with overscan 5', () => {
    render(<Harness sort={null} />);
    const before = lastOptions().getItemKey;
    expect(before(0)).toBe(IDS[0]);
    const region = container.querySelector<HTMLDivElement>('.tbl-wrap')!;
    act(() => region.focus());
    act(() => {
      region.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true, cancelable: true }));
    });
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[0]);
    const calls = virtualizerOptions.length;
    act(() => {
      region.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true, cancelable: true }));
    });
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[1]);
    expect(virtualizerOptions.length, 'precondition: the cursor move rendered the table').toBeGreaterThan(calls);
    expect(lastOptions().getItemKey).toBe(before);
    expect(lastOptions().overscan).toBe(5);
  });

  it('estimates 44px comfortable and 36px compact rows, plus the expanded preview', () => {
    render(<Harness sort={null} expandedId={IDS[1]} />);
    expect(lastOptions().estimateSize(0)).toBe(44);
    expect(lastOptions().estimateSize(1)).toBe(44 + LEAD_EXPANDED_PREVIEW_ESTIMATE_PX);

    app.density = 'compact';
    act(() => setTick?.(1));
    expect(lastOptions().estimateSize(0)).toBe(36);
    expect(lastOptions().estimateSize(1)).toBe(36 + LEAD_EXPANDED_PREVIEW_ESTIMATE_PX);
  });

  it('sorts once per rows and sort: a re-render with neither changed calls sortValue again zero times', () => {
    render(<Harness sort={{ key: 'equity', dir: 'desc' }} />);
    expect(counts.sortValue, 'precondition: the equity sort ran').toBeGreaterThan(0);
    const firstRow = container.querySelector('tr[data-borrower-row]')?.getAttribute('data-borrower-row');
    expect(firstRow).toBe(IDS[3]);
    const afterSort = counts.sortValue;

    act(() => setTick?.(1));
    act(() => setTick?.(2));

    expect(counts.sortValue).toBe(afterSort);
  });

  it('a row expand re-derives no other borrower\'s workflow states', () => {
    render(<LeadTable leads={LEADS} />);
    expect(new Set(workflowCalls), 'precondition: every row\'s Status cell read its states').toEqual(new Set(IDS));
    workflowCalls.length = 0;
    statusCellRenders.length = 0;

    click(container.querySelector(`[aria-label="Toggle preview for lead ${IDS[1]}"]`));

    expect(container.querySelector(`tr[data-borrower-row="${IDS[1]}"]`)?.classList.contains('is-expanded')).toBe(true);
    expect(workflowCalls, 'non-vacuity: the expanded row re-read its own states').toContain(IDS[1]);
    expect(workflowCalls.filter((id) => id !== IDS[1])).toEqual([]);
    expect(statusCellRenders, 'non-vacuity: the expanded row\'s Status cell re-rendered').toContain(IDS[1]);
    expect(statusCellRenders.filter((id) => id !== IDS[1])).toEqual([]);
  });

  it('an unrelated AppContext change re-derives no row\'s workflow states', () => {
    render(<Harness sort={null} />);
    workflowCalls.length = 0;
    statusCellRenders.length = 0;
    const before = counts.shellRenders;

    app.drawer = 'evidence-panel';
    act(() => setTick?.(1));

    expect(counts.shellRenders, 'precondition: the change re-rendered the table').toBeGreaterThan(before);
    expect(workflowCalls).toEqual([]);
    expect(statusCellRenders).toEqual([]);
  });

  it('a keystroke in the reject rationale never re-renders the table', () => {
    render(<LeadTable leads={LEADS} />);
    click(container.querySelector(`[data-testid="lead-reject-${IDS[0]}"]`));
    const rationale = container.querySelector<HTMLTextAreaElement>('form.decision-panel textarea');
    if (!rationale) throw new Error('the reject panel did not open');
    const before = counts.shellRenders;
    typeInto(rationale, 'hold note');
    expect(rationale.value).toBe('hold note');
    expect(counts.shellRenders).toBe(before);
  });

  it('an outcome and callback-time change in the disposition form never re-renders the table', () => {
    render(<LeadTable leads={LEADS} salesTeam={TEAM} />);
    click(container.querySelector(`[aria-label="Toggle preview for lead ${IDS[0]}"]`));
    click(container.querySelector(`[aria-label="Log call disposition for ${IDS[0]}"]`));
    const form = [...container.querySelectorAll<HTMLFormElement>('form.decision-panel')].find(
      (candidate) => candidate.textContent?.includes('Call disposition'),
    );
    if (!form) throw new Error('the disposition panel did not open');
    expect(form.querySelector('textarea'), 'the retired Notes field is gone').toBeNull();
    const before = counts.shellRenders;
    const outcome = [...form.querySelectorAll<HTMLSelectElement>('select')][1];
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(outcome, 'callback_scheduled');
      outcome.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const callback = form.querySelector<HTMLInputElement>('input[type="datetime-local"]');
    if (!callback) throw new Error('the callback time did not appear');
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(callback, '2026-07-15T09:30');
      callback.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(callback.value).toBe('2026-07-15T09:30');
    expect(counts.shellRenders).toBe(before);
  });

  it('a keystroke in the bulk gate\'s shared rationale never re-renders the table', () => {
    render(<LeadTable leads={LEADS} />);
    click(container.querySelector(`[data-testid="lead-select-${IDS[0]}"]`));
    click(container.querySelector(`[data-testid="lead-select-${IDS[1]}"]`));
    click(container.querySelector('[data-testid="lead-bulk-approve"]'));
    const rationale = container.querySelector<HTMLInputElement>('.bulk-actions__rationale input');
    if (!rationale) throw new Error('the bulk gate did not open');
    const before = counts.shellRenders;
    typeInto(rationale, 'Q3 sweep');
    expect(rationale.value).toBe('Q3 sweep');
    expect(counts.shellRenders).toBe(before);
  });
});
