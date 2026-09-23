import { Icon } from '../Icon';

/**
 * The row-approval result line (audit flow-03): the table's existing
 * `.table-success` + `role=status` toast pattern (the sales-ops toast), with
 * a "View receipt" action that opens the decided row on its Decision
 * receipt (the wave-1a ledger read-back, which Offer Orchestrator renders
 * in place the same way). It renders only after the approve write returned
 * ok, and it stays until the next decision or Dismiss: a toast carrying an
 * action never times out from under the keyboard user.
 */
export interface LeadDecisionToastState {
  borrowerId: string;
}

export function LeadTableDecisionToast({
  toast,
  hasReceipt,
  onViewReceipt,
  onDismiss,
}: {
  toast: LeadDecisionToastState;
  /** The write returned an audit id, so the row has a receipt to open. */
  hasReceipt: boolean;
  onViewReceipt: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="table-success lead-table__decision-toast" data-testid="lead-decision-toast">
      <span role="status" aria-live="polite">
        Approved <span className="mono">{toast.borrowerId}</span>.
      </span>
      {hasReceipt && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onViewReceipt}
          data-testid="lead-decision-view-receipt"
        >
          View receipt
        </button>
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
