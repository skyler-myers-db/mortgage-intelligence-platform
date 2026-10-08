/**
 * @vitest-environment happy-dom
 *
 * The bulk toolbar's own control states, rendered (LeadTableBulkActions):
 *
 *  - "Request approval for N" over the server's per-request cap (500, the
 *    create body's maxItems) is aria-disabled with the cap as its reason, so
 *    the person is never sent to a schema 422 that reads "Shorten the
 *    reason" (flow-02 / shell-06).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { mount } from '../../test/render';
import { LeadTableBulkActions, MAX_APPROVAL_REQUEST_BORROWERS, REQUEST_OVER_CAP } from './LeadTableBulkActions';

interface Options {
  eligible: number;
  runKind?: 'approve' | 'reject' | null;
  onOpen?: () => void;
  blockedReason?: string | null;
}

async function renderToolbar({ eligible, runKind = null, onOpen = () => undefined, blockedReason = null }: Options) {
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
      requestApproval={{ blockedReason, onOpen }}
    />,
  );
}

const byTestId = (id: string) => document.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`)!;

describe('LeadTableBulkActions: Request approval over the cap', () => {
  it('is live at the cap', async () => {
    const onOpen = vi.fn();
    await renderToolbar({ eligible: MAX_APPROVAL_REQUEST_BORROWERS, onOpen });
    const button = byTestId('lead-bulk-request-approval');
    expect(button.textContent).toBe(`Request approval for ${MAX_APPROVAL_REQUEST_BORROWERS}`);
    expect(button.getAttribute('aria-disabled')).toBeNull();
    expect(document.querySelector('[data-testid="lead-bulk-request-blocked"]')).toBeNull();
    button.click();
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('is aria-disabled with the cap as its reason one over it, and opens nothing', async () => {
    const onOpen = vi.fn();
    await renderToolbar({ eligible: MAX_APPROVAL_REQUEST_BORROWERS + 1, onOpen });
    const button = byTestId('lead-bulk-request-approval');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(button.disabled, 'never native disabled').toBe(false);
    const note = document.getElementById(button.getAttribute('aria-describedby')!)!;
    expect(note.textContent).toBe(REQUEST_OVER_CAP);
    expect(REQUEST_OVER_CAP).toBe('A request names at most 500 borrowers. Select fewer to request approval.');
    button.click();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('is the create body\'s maxItems in the committed OpenAPI baseline', () => {
    const baseline = JSON.parse(
      readFileSync(join(process.cwd(), '..', 'tests', 'fixtures', 'openapi_baseline.json'), 'utf8'),
    ) as { components: { schemas: Record<string, { properties: Record<string, { maxItems?: number }> }> } };
    const borrowerIds = baseline.components.schemas.ApprovalRequestCreate.properties.borrower_ids;
    expect(borrowerIds.maxItems).toBe(MAX_APPROVAL_REQUEST_BORROWERS);
  });

  it('a campaign binding still names its own reason over the cap', async () => {
    await renderToolbar({ eligible: MAX_APPROVAL_REQUEST_BORROWERS + 1, blockedReason: 'Clear the campaign binding.' });
    const button = byTestId('lead-bulk-request-approval');
    expect(document.getElementById(button.getAttribute('aria-describedby')!)!.textContent).toBe('Clear the campaign binding.');
  });
});
