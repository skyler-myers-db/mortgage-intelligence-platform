/**
 * @vitest-environment happy-dom
 *
 * "Plan distribution" from the Lead Queue's bulk toolbar (wow-power-5, W5e
 * correction C10), on the rendered LeadTable:
 *  - a loan officer (a non-empty scoped roster, no manager role) never sees
 *    it, so never opens a Board that answers 403;
 *  - a sales manager sees it, and it opens the lazy Board on the selection;
 *  - the Board is reached only through React.lazy: no module of the
 *    LeadTable chunk imports DistributionBoard statically;
 *  - the gate reads the roster the route cached and fetches nothing.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the component sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import type { LeadSummary, SalesTeamMember } from '../../types';
import { queryKeys } from '../../lib/queryKeys';
import { installLocalStorage } from '../../test/installLocalStorage';

declare const process: { cwd(): string };

const actor = vi.hoisted(() => ({ email: 'manager.one@summit.example' }));
const boards = vi.hoisted(() => [] as Array<string[] | undefined>);
const salesTeam = vi.hoisted(() => vi.fn());

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: () => undefined,
    setLastBorrowerId: () => undefined,
    openConsoleRecentActivity: () => undefined,
    setDrawer: () => undefined,
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: actor.email,
    sessionStatus: 'ready',
    density: 'comfortable',
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: vi.fn(),
    approve: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam,
  },
}));

vi.mock('./DistributionBoard', () => ({
  default: ({ borrowerIds }: { borrowerIds?: string[] }) => {
    boards.push(borrowerIds);
    return <div data-testid="distribution-board-stub">{borrowerIds?.length}</div>;
  },
}));

import { LeadTable } from './LeadTable';

const MANAGER: SalesTeamMember = {
  email: 'manager.one@summit.example', display_label: 'Sales Manager', role: 'sales_manager',
  region: 'Midwest', manager_email: null, capacity_per_day: 0, active: true,
};
const LO: SalesTeamMember = {
  email: 'lo.alpha@summit.example', display_label: 'Loan Officer A', role: 'loan_officer',
  region: 'Midwest', manager_email: MANAGER.email, capacity_per_day: 25, active: true,
};

function lead(index: number): LeadSummary {
  return {
    borrower_id: `B-DISTRIB${String(index).padStart(6, '0')}`,
    clip: `clip_${index}`,
    display_name: `Owner ${index}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 90 - index,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'approved',
    outreach_status: 'none',
  } as unknown as LeadSummary;
}

const LEADS = [lead(1), lead(2), lead(3)];

describe('LeadTable: Plan distribution', () => {
  let container: HTMLDivElement;
  let root: Root;

  function render(roster: SalesTeamMember[]) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    // The route's roster read, already in the cache (useSalesRoster's key).
    queryClient.setQueryData(queryKeys.salesRoster(), roster);
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={LEADS} salesTeam={roster.filter((member) => member.role === 'loan_officer')} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function selectAll() {
    for (const row of LEADS) {
      act(() => container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${row.borrower_id}"]`)!.click());
    }
  }

  beforeEach(() => {
    installLocalStorage();
    boards.length = 0;
    salesTeam.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('a loan officer with a non-empty scoped roster never sees it', () => {
    actor.email = LO.email;
    render([LO]);
    selectAll();
    expect(container.querySelector('[data-testid="lead-bulk-actions"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="lead-bulk-plan-distribution"]')).toBeNull();
    expect(salesTeam).not.toHaveBeenCalled();
  });

  it('a sales manager opens the lazy Board on the selected rows', async () => {
    actor.email = MANAGER.email;
    render([MANAGER, LO]);
    selectAll();
    const button = container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-plan-distribution"]');
    expect(button).not.toBeNull();
    act(() => button!.click());
    await vi.waitFor(() => {
      expect(document.querySelector('[data-testid="distribution-board-stub"]')).not.toBeNull();
    });
    expect(boards[boards.length - 1]?.slice().sort()).toEqual(LEADS.map((row) => row.borrower_id).sort());
    expect(salesTeam).not.toHaveBeenCalled();
  });

  it('reaches the Board only through React.lazy (no static import in the LeadTable chunk)', () => {
    const dir = `${process.cwd()}/src/components/mortgage/`;
    const files = (readdirSync(dir) as string[]).filter((name) => /\.(tsx?)$/.test(name) && !/\.test\.tsx?$/.test(name));
    const importers = files.filter((name) => name !== 'DistributionBoard.tsx'
      && /^\s*import[^;]*from\s+'\.\/DistributionBoard'/m.test(readFileSync(`${dir}${name}`, 'utf8') as string));
    expect(files.length).toBeGreaterThan(100);
    expect(importers).toEqual([]);
    const opener = readFileSync(`${dir}LeadTable.distribution.tsx`, 'utf8') as string;
    expect(opener).toContain("lazy(() => import('./DistributionBoard'))");
  });
});
