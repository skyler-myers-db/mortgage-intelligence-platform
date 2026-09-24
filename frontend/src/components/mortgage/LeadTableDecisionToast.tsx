import { Icon } from '../Icon';

/**
 * The row-approval result line (audit flow-03): the table's `.table-success`
 * toast, with a "View receipt" action that opens the decided row on its
 * Decision receipt (the wave-1a ledger read-back, which Offer Orchestrator
 * renders in place the same way). It renders only after the approve write
 * returned ok, and it stays until the next decision or Dismiss: a toast
 * carrying an action never times out from under the keyboard user.
 *
 * It is not itself a live region: a `role=status` inserted already holding
 * its message can go unannounced, so LeadTable speaks the same line from a
 * live region that is always mounted (`lead-decision-status`).
 *
 * A queue filtered to pending rows drops the approved row on its next
 * refetch; "View receipt" would then have no row to open. The line says so
 * and offers Recent activity, where the approval's audit event is listed.
 */
export interface LeadDecisionToastState {
  borrowerId: string;
}

export function LeadTableDecisionToast({
  toast,
  hasReceipt,
  rowListed,
  onViewReceipt,
  onReviewRecentActivity,
  onDismiss,
}: {
  toast: LeadDecisionToastState;
  /** The write returned an audit id, so the row has a receipt to open. */
  hasReceipt: boolean;
  /** The approved row is still in the loaded list (a pending-only filter drops it). */
  rowListed: boolean;
  onViewReceipt: () => void;
  onReviewRecentActivity: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="table-success lead-table__decision-toast" data-testid="lead-decision-toast">
      <span>
        Approved <span className="mono">{toast.borrowerId}</span>.
      </span>
      {hasReceipt && rowListed && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onViewReceipt}
          data-testid="lead-decision-view-receipt"
        >
          View receipt
        </button>
      )}
      {!rowListed && (
        <>
          <span className="muted" data-testid="lead-decision-row-left">
            It left this filtered list; its audit event is in Recent activity.
          </span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onReviewRecentActivity}
            data-testid="lead-decision-recent-activity"
          >
            Review recent activity
          </button>
        </>
      )}
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onDismiss}
        aria-label={`Dismiss the approval message for ${toast.borrowerId}`}
      >
        <Icon name="close" size={12} />
      </button>
    </div>
  );
}
