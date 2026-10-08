/**
 * @vitest-environment happy-dom
 *
 * The bulk toolbar's own control states, rendered (LeadTableBulkActions):
 *
 *  - "Request approval for N" over the server's per-request cap (500, the
 *    create body's maxItems) is aria-disabled with the cap as its reason, so
 *    the person is never sent to a schema 422 that reads "Shorten the
 *    reason" (flow-02 / shell-06).
 *  - A run's own button keeps Button loading and is never native disabled,
 *    even when its last group leaves no selected row eligible (in-flight rows
 *    are not eligible): a focused button that turns disabled drops focus to
 *    <body> (motion-08 slice 2).
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the committed OpenAPI baseline under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { mount } from '../../test/render';
import { LeadTableBulkActions, MAX_APPROVAL_REQUEST_BORROWERS, REQUEST_OVER_CAP } from './LeadTableBulkActions';
import type { BulkRunKind } from './useLeadBulkRun';

interface Options {
  eligible: number;
  runKind?: BulkRunKind | null;
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

declare const process: { cwd(): string };

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
      readFileSync(join(process.cwd(), '..', 'tests', 'fixtures', 'openapi_baseline.json'), 'utf8') as string,
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
