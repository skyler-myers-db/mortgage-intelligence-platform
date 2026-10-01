import { formatCount } from '../../lib/formatters';
import { bulkRunSummary, bulkRunVerb, type BulkRunProgress, type BulkRunResult } from './useLeadBulkRun';

/**
 * A bulk run's progress and result when the lazy bulk chunk (which ships
 * LeadBulkRunProgress / LeadBulkRunResult) failed to load (audit states-07
 * item 2; w3-queue-place #9c). A run is already on the wire or finished by
 * then, so the reader still needs the count, the only Stop and the outcome:
 * a static line from the LeadTable chunk, in the same `.bulk-actions` BEM,
 * with plain `.btn` markup (the Button primitive is not needed for two
 * buttons). The live region still speaks the start, quarters and result.
 * Every line names the run's kind (approve or reject, tables-07).
 */
export function LeadBulkRunProgressFallback({
  progress,
  onStop,
}: {
  progress: BulkRunProgress;
  onStop: () => void;
}) {
  return (
    <div className="bulk-actions__run" data-testid="lead-bulk-run-fallback">
      <span className="bulk-actions__label">
        {bulkRunVerb(progress.kind)} {formatCount(progress.settled)} of {formatCount(progress.total)}
      </span>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onStop}
        aria-disabled={progress.stopRequested || undefined}
        data-testid="lead-bulk-stop"
      >
        {progress.stopRequested ? 'Stopping after this batch…' : 'Stop after this batch'}
      </button>
    </div>
  );
}

export function LeadBulkRunResultFallback({
  result,
  onDismiss,
}: {
  result: BulkRunResult;
  onDismiss: () => void;
}) {
  return (
    <div className="bulk-actions bulk-actions__result" data-testid="lead-bulk-result">
      <span className="bulk-actions__toast">{bulkRunSummary(result)}</span>
      <button type="button" className="btn btn--ghost btn--sm" onClick={onDismiss} data-testid="lead-bulk-result-dismiss">
        Dismiss
      </button>
    </div>
  );
}
