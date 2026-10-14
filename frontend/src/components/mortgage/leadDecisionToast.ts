/**
 * The Lead Queue's row-approval result on the shell toast region (audit
 * states-07 item 1; deviation:toast-actions-and-path). It replaces the table's
 * own `.lead-table__decision-toast` line and its second live region: the
 * Toaster announces it once, keeps it (info never auto-dismisses), links the
 * APPROVE audit row and moves into the topmost dialog.
 *
 * One at a time: a new approval dismisses the previous Lead Queue decision
 * toast (module state, so it survives the table's keep-alive remount). Its one
 * action is "View receipt" when the approved row is listed and its write
 * returned an audit id: it opens the row on its Decision receipt when the table
 * is mounted and visible, else returns to the queue URL that showed it with
 * `?row=<id>`; a row that has since left the filtered list goes to Recent
 * activity instead (never a dead link). Without a receipt, or for a row not
 * listed, the action is "Review recent activity". Raised only after the
 * approve returned ok (pessimistic); shown only to the signed-in person.
 */
import { dismissToast, toast } from '../../lib/toast';

export const DECISION_LEFT_LIST = 'It left this filtered list; its audit event is in Recent activity.';

export interface DecisionToastInput {
  borrowerId: string;
  auditEventId: string | null;
  /** The row is in the loaded list now (when the toast is raised). */
  listed: boolean;
  /** Read again on the action: a refetch may have dropped the row. */
  isListed: () => boolean;
  /** Open the row on its receipt; false when the table cannot show it now. */
  viewReceipt: () => boolean;
  /** Return to the queue URL that showed the row, naming it (`?row=`). */
  navigateToRow: () => void;
  openRecentActivity?: () => void;
}

let currentToastId: number | null = null;

export function raiseDecisionToast(input: DecisionToastInput): void {
  if (currentToastId !== null) dismissToast(currentToastId);
  const title = `Approved ${input.borrowerId}`;
  const recentActivity = input.openRecentActivity
    ? { label: 'Review recent activity', onAction: input.openRecentActivity }
    : null;
  if (input.listed && input.auditEventId) {
    currentToastId = toast.info(title, {
      auditEventId: input.auditEventId,
      action: {
        label: 'View receipt',
        onAction: () => {
          if (!input.isListed()) input.openRecentActivity?.();
          else if (!input.viewReceipt()) input.navigateToRow();
        },
      },
    });
    return;
  }
  currentToastId = toast.info(title, {
    auditEventId: input.auditEventId,
    detail: input.listed ? null : DECISION_LEFT_LIST,
    action: recentActivity,
  });
}

/** Test seam: forget the toast a previous test raised. */
export function _resetDecisionToastForTests(): void {
  currentToastId = null;
}
