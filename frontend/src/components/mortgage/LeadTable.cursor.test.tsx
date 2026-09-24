/**
 * @vitest-environment happy-dom
 *
 * The ranked-borrower table's keyboard row cursor (audit tables-03): an
 * active row on the NATIVE table (no role=grid, no roving tabindex). J / K
 * and the arrows move it, Enter opens and closes the cursor row, X selects
 * it, A / R act on it; the keys work only with focus inside `.tbl-wrap`, and
 * never while a dialog is open or single-key shortcuts are switched off.
 * Moving the cursor or opening a row never drafts: `/outreach/draft` writes
 * a DRAFT_OUTREACH audit row.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const draftOutreach = vi.fn();
const approve = vi.fn();
const reject = vi.fn();

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
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
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    reject: (...args: unknown[]) => reject(...args),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

const IDS = ['B-CURSOR0000001', 'B-CURSOR0000002', 'B-CURSOR0000003', 'B-CURSOR0000004'];

function lead(borrowerId: string, approvalStatus: LeadSummary['approval_status'] = 'pending'): LeadSummary {
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
    opportunity_score: 88,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: approvalStatus,
  } as unknown as LeadSummary;
}

describe('LeadTable keyboard row cursor', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-reject' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // The second row is already approved: X / A / the advance skip it.
    const leads = [lead(IDS[0]), lead(IDS[1], 'approved'), lead(IDS[2]), lead(IDS[3])];
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={leads} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const region = () => container.querySelector<HTMLDivElement>('.tbl-wrap')!;
  const cursorRow = () => container.querySelector<HTMLTableRowElement>('tr.is-cursor');
  const row = (id: string) => container.querySelector<HTMLTableRowElement>(`tr[data-borrower-row="${id}"]`)!;

  function press(key: string, init: KeyboardEventInit = {}, target: Element = document.activeElement ?? region()) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
    act(() => {
      target.dispatchEvent(event);
    });
    return event;
  }

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  it('J / K and the arrows move one cursor row, announce it and stay inside the list', () => {
    region().focus();
    expect(cursorRow()).toBeNull();

    const first = press('j');
    expect(first.defaultPrevented).toBe(true);
    expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[0]);
    expect(cursorRow()?.getAttribute('aria-current')).toBe('true');
    expect(container.querySelector('[data-testid="lead-cursor-status"]')?.textContent)
      .toBe(`Row 1 of 4: ${IDS[0]}, Chicago, IL, Refinance review, score 88, pending.`);

    press('ArrowDown');
    press('j');
    expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[2]);
    press('k');
    expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[1]);
    press('ArrowUp');
    press('ArrowUp');
    expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[0]);
    expect(container.querySelectorAll('tr.is-cursor')).toHaveLength(1);
    // No role=grid rewrite, no roving tabindex: rows are not tab stops.
    expect(container.querySelector('[role="grid"]')).toBeNull();
    expect(row(IDS[0]).hasAttribute('tabindex')).toBe(false);
    expect(draftOutreach).not.toHaveBeenCalled();
  });

  it('J from a control in a row moves focus to the scroll region (a virtualized row may unmount)', () => {
    const borrowerButton = row(IDS[0]).querySelector<HTMLButtonElement>('.lead-table__borrower-btn')!;
    borrowerButton.focus();
    press('j', {}, borrowerButton);
    expect(document.activeElement).toBe(region());
    expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[0]);
  });

  it('Enter opens and closes the cursor row, and never drafts', () => {
    region().focus();
    press('j');
    press('Enter');
    expect(row(IDS[0]).classList.contains('is-expanded')).toBe(true);
    expect(container.querySelector('.tbl__expand')).not.toBeNull();
    press('Enter');
    expect(container.querySelector('.tbl__expand')).toBeNull();
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
  });

  it('Enter on a control inside the table is the control, not the row shortcut', () => {
    region().focus();
    press('j');
    const reject = row(IDS[0]).querySelector<HTMLButtonElement>(`[data-testid="lead-reject-${IDS[0]}"]`)!;
    reject.focus();
    const event = press('Enter', {}, reject);
    expect(event.defaultPrevented).toBe(false);
    expect(container.querySelector('.tbl__expand')).toBeNull();
  });

  it('X selects and clears the cursor row, and skips a row that cannot be selected', () => {
    region().focus();
    press('j');
    press('x');
    const checkbox = (id: string) => container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id}"]`)!;
    expect(checkbox(IDS[0]).checked).toBe(true);
    press('x');
    expect(checkbox(IDS[0]).checked).toBe(false);
  });

  it('advertises the keys on the region and the cursor row only', () => {
    expect(region().getAttribute('aria-keyshortcuts')).toBe('J ArrowDown K ArrowUp Enter X');
    region().focus();
    press('j');
    const approveButton = container.querySelector(`[data-testid="lead-approve-${IDS[0]}"]`);
    const otherApprove = container.querySelector(`[data-testid="lead-approve-${IDS[2]}"]`);
    expect(approveButton?.getAttribute('aria-keyshortcuts')).toBe('A');
    expect(otherApprove?.getAttribute('aria-keyshortcuts')).toBeNull();
    expect(container.querySelector(`[data-testid="lead-select-${IDS[0]}"]`)?.getAttribute('aria-keyshortcuts')).toBe('X');
  });

  it('R opens the reject panel for the cursor row, and a returned reject advances to the next pending row', async () => {
    region().focus();
    press('j');
    press('r');
    const panel = container.querySelector('.decision-panel');
    expect(panel?.textContent).toContain(IDS[0]);

    const form = panel as HTMLFormElement;
    await act(async () => {
      form.requestSubmit();
    });
    await flush();

    expect(reject).toHaveBeenCalledWith(IDS[0], expect.objectContaining({ rationale_code: 'low_intent' }));
    // IDS[1] is already approved: the cursor lands on the next PENDING row.
    expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[2]);
  });

  it('a skip link jumps past the rows', () => {
    const skip = container.querySelector<HTMLAnchorElement>('a.lead-table__skip');
    expect(skip?.textContent).toBe('Skip table');
    const target = document.getElementById(skip!.getAttribute('href')!.slice(1));
    expect(target?.getAttribute('tabindex')).toBe('-1');
    // The target sits after the scroll region.
    expect(region().compareDocumentPosition(target!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Activating it moves focus there without a fragment navigation.
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => {
      skip!.dispatchEvent(click);
    });
    expect(click.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(target);
  });

  describe('scope', () => {
    it('does nothing with focus outside the table or while a dialog is open', () => {
      const outside = document.createElement('button');
      document.body.appendChild(outside);
      outside.focus();
      press('j', {}, outside);
      expect(cursorRow()).toBeNull();
      outside.remove();

      region().focus();
      const dialog = document.createElement('div');
      dialog.setAttribute('role', 'dialog');
      document.body.appendChild(dialog);
      press('j');
      expect(cursorRow()).toBeNull();
      dialog.remove();
    });

    it('is off, keys and advertisement, with the single-key switch off', () => {
      act(() => setSingleKeyShortcutsEnabled(false));
      region().focus();
      const event = press('j');
      expect(event.defaultPrevented).toBe(false);
      expect(cursorRow()).toBeNull();
      expect(region().getAttribute('aria-keyshortcuts')).toBeNull();
      expect(container.textContent).toContain('Single-key shortcuts are off');
    });

    it('leaves A, R and Shift+A inert with the switch off, even on a clicked cursor row (tables-v2, a11y-09)', async () => {
      act(() => setSingleKeyShortcutsEnabled(false));
      // Two pending rows selected, so Shift+A would otherwise open the bulk gate.
      for (const id of [IDS[2], IDS[3]]) {
        const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id}"]`)!;
        act(() => checkbox.click());
      }
      // A mouse click still puts the cursor on a row and expands it.
      act(() => row(IDS[0]).click());
      expect(cursorRow()?.getAttribute('data-borrower-row')).toBe(IDS[0]);
      region().focus();

      const pressed = [press('a'), press('r'), press('A', { shiftKey: true })];
      await flush();

      expect(pressed.map((event) => event.defaultPrevented)).toEqual([false, false, false]);
      expect(container.querySelector('[data-testid="lead-approve-review"]')).toBeNull();
      expect(document.querySelector('dialog.lead-approve-dialog')).toBeNull();
      expect(container.querySelector('.decision-panel')).toBeNull();
      expect(container.querySelector('.bulk-actions__rationale')).toBeNull();
      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
      expect(reject).not.toHaveBeenCalled();
      // Nothing advertises a key that does nothing.
      expect(container.querySelector(`[data-testid="lead-approve-${IDS[0]}"]`)?.getAttribute('aria-keyshortcuts')).toBeNull();
      expect(container.querySelector(`[data-testid="lead-reject-${IDS[0]}"]`)?.getAttribute('aria-keyshortcuts')).toBeNull();
      expect(container.querySelector('[data-testid="lead-bulk-approve"]')?.getAttribute('aria-keyshortcuts')).toBeNull();
    });
  });
});
