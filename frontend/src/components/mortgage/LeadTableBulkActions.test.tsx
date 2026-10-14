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
import { describe, expect, it } from 'vitest';
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
