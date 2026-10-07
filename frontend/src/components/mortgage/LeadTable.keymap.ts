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
  extendSelect: ['Shift+X'],
  approve: ['a'],
  reject: ['r'],
  bulkApprove: ['Shift+A'],
  bulkReject: ['Shift+R'],
} as const satisfies Record<string, readonly string[]>;

export interface LeadTableKeymapActions {
  /** A / R / Shift+A / Shift+R are registered only when this is true. */
  approverActive: boolean;
  /** The Triage deck is open: the table registers no keys (its sheet lists the deck's). */
  suspended?: boolean;
  move: (delta: 1 | -1) => void;
  /** Expand or collapse the cursor row; false when there is none. */
  toggleCursorRow: () => boolean;
  toggleSelectCursorRow: () => void;
  /** Select from the selection anchor (the last plain toggle) to the cursor row. */
  extendSelectionToCursor: () => void;
  reviewCursorRow: () => void;
  rejectCursorRow: () => void;
  openBulkGate: () => void;
  /** Shift+R: the bulk reject gate (one row: its reject panel); never submits. */
  openBulkRejectGate: () => void;
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
  if (actions.suspended) return [];
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
      // One row on the `?` sheet for X and Shift+X (a separate row made the
      // sheet's list scroll at 1440x900, and a scrolling list is a keyboard
      // trap for axe's scrollable-region-focusable).
      id: 'select',
      keys: [...LEAD_TABLE_KEYS.select, ...LEAD_TABLE_KEYS.extendSelect],
      description: 'Select or clear the borrower; with Shift, select every borrower from the last one you selected',
      run: (event) => (event.shiftKey ? actions.extendSelectionToCursor() : actions.toggleSelectCursorRow()),
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
      // One `?` sheet row for Shift+A and Shift+R (tables-07), like X and
      // Shift+X: a separate row, or a description that wraps, made the
      // sheet's list scroll at 1440x900 (axe scrollable-region-focusable).
      id: 'bulk-approve',
      keys: [...LEAD_TABLE_KEYS.bulkApprove, ...LEAD_TABLE_KEYS.bulkReject],
      description: 'Approve or reject the selected borrowers (opens a gate)',
      run: (event) => (event.key.toLowerCase() === 'r' ? actions.openBulkRejectGate() : actions.openBulkGate()),
    },
  ];
}
