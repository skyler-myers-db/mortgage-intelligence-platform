/**
 * The ranked-borrower table's shortcuts (audit tables-03, wow-power-4):
 * which keys, what the `?` sheet calls them, and what they do. Handlers act
 * on the cursor row; the approval keys exist only for an approver.
 */
import type { LeadTableHotkey } from './useLeadTableHotkeys';

export const LEAD_TABLE_KEYS = {
  next: ['j', 'ArrowDown'],
  previous: ['k', 'ArrowUp'],
  toggle: ['Enter'],
  select: ['x'],
  approve: ['a'],
  reject: ['r'],
  bulkApprove: ['Shift+A'],
} as const satisfies Record<string, readonly string[]>;

export interface LeadTableKeymapActions {
  /** A / R / Shift+A are registered only when this is true. */
  approverActive: boolean;
  move: (delta: 1 | -1) => void;
  /** Expand or collapse the cursor row; false when there is none. */
  toggleCursorRow: () => boolean;
  toggleSelectCursorRow: () => void;
  reviewCursorRow: () => void;
  rejectCursorRow: () => void;
  openBulkGate: () => void;
}

const NATIVE_ACTIVATION = 'button, a[href], summary, [role="button"], [role="link"], [role="checkbox"]';

/** Enter on a control activates the control, never the row shortcut. */
function isNativeActivation(event: KeyboardEvent, scope: HTMLElement | null): boolean {
  const target = event.target instanceof Element ? event.target : null;
  if (!target || target === scope) return false;
  const control = target.closest(NATIVE_ACTIVATION);
  return control !== null && control !== scope;
}

export function leadTableHotkeys(actions: LeadTableKeymapActions): LeadTableHotkey[] {
  const hotkeys: LeadTableHotkey[] = [
    { id: 'next', keys: LEAD_TABLE_KEYS.next, description: 'Next borrower', run: () => actions.move(1) },
    { id: 'previous', keys: LEAD_TABLE_KEYS.previous, description: 'Previous borrower', run: () => actions.move(-1) },
    {
      id: 'toggle',
      keys: LEAD_TABLE_KEYS.toggle,
      description: 'Open or close the borrower preview',
      run: (event, scope) => (isNativeActivation(event, scope) ? false : actions.toggleCursorRow()),
    },
    {
      id: 'select',
      keys: LEAD_TABLE_KEYS.select,
      description: 'Select or clear the borrower',
      run: () => actions.toggleSelectCursorRow(),
    },
  ];
  if (!actions.approverActive) return hotkeys;
  return [
    ...hotkeys,
    {
      id: 'approve',
      keys: LEAD_TABLE_KEYS.approve,
      description: 'Review the outreach, then approve (Enter confirms)',
      run: () => actions.reviewCursorRow(),
    },
    { id: 'reject', keys: LEAD_TABLE_KEYS.reject, description: 'Reject with a reason', run: () => actions.rejectCursorRow() },
    {
      id: 'bulk-approve',
      keys: LEAD_TABLE_KEYS.bulkApprove,
      description: 'Approve the selected borrowers (opens the rationale gate)',
      run: () => actions.openBulkGate(),
    },
  ];
}
