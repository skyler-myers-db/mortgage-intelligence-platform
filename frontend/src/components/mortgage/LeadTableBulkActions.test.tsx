/**
 * @vitest-environment happy-dom
 *
 * The bulk toolbar's own control states, rendered (LeadTableBulkActions): a
 * run's own button keeps Button loading and is never native disabled, even
 * when its last group leaves no selected row eligible (in-flight rows are
 * not eligible): a focused button that turns disabled drops focus to <body>
 * (motion-08 slice 2). The 500-borrower request cap is the lazy form's to
 * refuse (RequestApprovalForm.test.tsx), so its copy stays out of the
 * LeadTable chunk.
 */
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { SalesTeamMember } from '../../types';
import { mount } from '../../test/render';
import { LeadTableBulkActions } from './LeadTableBulkActions';
import type { BulkRunKind } from './useLeadBulkRun';

async function renderToolbar({ eligible, runKind = null, requestFormOpen = false }: {
  eligible: number;
  runKind?: BulkRunKind | null;
  requestFormOpen?: boolean;
}) {
  return mount(
    <LeadTableBulkActions
      selectionCount={Math.max(eligible, 2)}
      selectedApprovalEligibleCount={eligible}
      bulkApproving={runKind !== null}
      runKind={runKind}
      bulkRationaleOpen={false}
      campaignBindingBlocked={false}
      salesTeam={[]}
      salesBusy={false}
      selectedAssignee=""
      onSelectedAssigneeChange={() => undefined}
      onAssign={() => undefined}
      onClearSelection={() => undefined}
      onBulkApprove={() => Promise.resolve(true)}
      bulkApproveBtnRef={createRef<HTMLButtonElement>()}
      requestFormOpen={requestFormOpen}
    />,
  );
}

const byTestId = (id: string) => document.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`)!;

describe('LeadTableBulkActions: a run\'s own button is never native disabled', () => {
  const cases: Array<[BulkRunKind, string, string]> = [
    ['approve', 'lead-bulk-approve', 'lead-bulk-reject'],
    ['reject', 'lead-bulk-reject', 'lead-bulk-approve'],
  ];
  it.each(cases)('a %s run whose last group left nothing eligible: loading, focusable; the other kind disabled', async (kind, own, other) => {
    await renderToolbar({ eligible: 0, runKind: kind });
    const button = byTestId(own);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    button.focus();
    expect(document.activeElement).toBe(button);
    expect(byTestId(other).disabled).toBe(true);
  });

  it('with no run, nothing eligible still disables both natively', async () => {
    await renderToolbar({ eligible: 0 });
    expect(byTestId('lead-bulk-approve').disabled).toBe(true);
    expect(byTestId('lead-bulk-reject').disabled).toBe(true);
  });
});

describe('LeadTableBulkActions: the toolbar request form takes the gate layout', () => {
  // The rendered row (the form on the full line under the controls) is
  // approval-requests.fixture.spec.ts (n); this pins the toolbar's half.
  it.each([[true, true], [false, false]])('requestFormOpen=%s: bulk-actions--gate is %s', async (open, gate) => {
    await renderToolbar({ eligible: 2, requestFormOpen: open });
    expect(document.querySelector('[data-testid="lead-bulk-actions"]')?.classList.contains('bulk-actions--gate')).toBe(gate);
  });
});

describe('LeadTableBulkActions: Plan distribution (wow-power-5)', () => {
  const TEAM: SalesTeamMember[] = [
    { email: 'lo.alpha@summit.example', display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: null, capacity_per_day: 25, active: true },
  ];

  async function renderPlanner(selectionCount: number, planDistribution: { onOpen: () => void } | null) {
    return mount(
      <LeadTableBulkActions
        selectionCount={selectionCount}
        selectedApprovalEligibleCount={0}
        bulkApproving={false}
        bulkRationaleOpen={false}
        campaignBindingBlocked={false}
        salesTeam={TEAM}
        salesBusy={false}
        selectedAssignee={TEAM[0].email}
        onSelectedAssigneeChange={() => undefined}
        onAssign={() => undefined}
        onClearSelection={() => undefined}
        onBulkApprove={() => Promise.resolve(true)}
        bulkApproveBtnRef={createRef<HTMLButtonElement>()}
        planDistribution={planDistribution}
      />,
    );
  }

  it('is absent without the planner gate', async () => {
    await renderPlanner(3, null);
    expect(document.querySelector('[data-testid="lead-bulk-plan-distribution"]')).toBeNull();
  });

  it('opens the Board on a click, inside the cap', async () => {
    const onOpen = vi.fn();
    await renderPlanner(500, { onOpen });
    const button = byTestId('lead-bulk-plan-distribution');
    expect(button.getAttribute('aria-disabled')).toBeNull();
    button.click();
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('is aria-disabled with a spoken reason past the 500-borrower cap, and opens nothing', async () => {
    const onOpen = vi.fn();
    await renderPlanner(501, { onOpen });
    const button = byTestId('lead-bulk-plan-distribution');
    expect(button.disabled).toBe(false);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    const note = document.getElementById(button.getAttribute('aria-describedby') ?? '');
    expect(note?.className).toBe('sr-only');
    expect(note?.textContent).toBe('Plan distribution takes at most 500 selected leads.');
    button.click();
    expect(onOpen).not.toHaveBeenCalled();
  });
});
