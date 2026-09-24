import { formatCount } from '../../lib/formatters';
import { Button } from '../Primitives';
import {
  bulkRunSummary,
  formatMinutesLeft,
  type BulkRunIssue,
  type BulkRunProgress,
  type BulkRunResult,
} from './useLeadBulkRun';

/**
 * A bulk run's progress and its per-row report (audit tables-07, states-08
 * bulk half). Shipped in the lazy bulk chunk (re-exported from
 * LeadBulkApproveReview.tsx); its CSS is in LeadBulkApproveReview.css.
 *
 * BEM extension of the app's `.bulk-actions` toolbar (it has no prototype
 * counterpart): `.bulk-actions__run` (progress line), `.bulk-actions__progress`
 * (the native <progress>), `.bulk-actions__result` and `.bulk-actions__issues`.
 * The announcements (start, each quarter, Stop, result) are spoken by the
 * table's always-mounted live region, not from here: a region inserted
 * already filled can go unannounced.
 */

const ISSUE_REASON: Record<BulkRunIssue['outcome'], string> = {
  backend: 'refused',
  network: 'the request never reached the server; retry it',
  session_expired: 'the session ended; not recorded',
  duplicate: 'skipped: a decision was already on the wire',
};

function issueReason(issue: BulkRunIssue): string {
  if (issue.outcome === 'backend' && issue.message) return `refused: ${issue.message}`;
  return ISSUE_REASON[issue.outcome];
}

export function LeadBulkRunProgress({
  progress,
  onStop,
}: {
  progress: BulkRunProgress;
  onStop: () => void;
}) {
  return (
    <div className="bulk-actions__run" data-testid="lead-bulk-run">
      <span className="bulk-actions__label">
        Approving{' '}
        <span className="mono num" data-testid="lead-bulk-run-count">
          {formatCount(progress.settled)} of {formatCount(progress.total)}
        </span>
      </span>
      <progress
        className="bulk-actions__progress"
        value={progress.settled}
        max={progress.total}
        aria-label="Approving selected borrowers"
      />
      {progress.minutesLeft !== null && (
        <span className="muted fs-12" data-testid="lead-bulk-run-eta">{formatMinutesLeft(progress.minutesLeft)}</span>
      )}
      <Button
        variant="ghost"
        size="sm"
        onClick={onStop}
        // aria-disabled, never native `disabled`: the focused button must
        // not drop keyboard focus to <body> once the Stop is recorded.
        aria-disabled={progress.stopRequested || undefined}
        data-testid="lead-bulk-stop"
      >
        {progress.stopRequested ? 'Stopping after this batch…' : 'Stop after this batch'}
      </Button>
    </div>
  );
}

export function LeadBulkRunResult({
  result,
  onDismiss,
}: {
  result: BulkRunResult;
  onDismiss: () => void;
}) {
  const issues = [...result.failed, ...result.skipped];
  const tone = result.failed.length > 0 || result.sessionEnded
    ? 'bulk-actions__toast--danger'
    : result.notStarted.length > 0 || result.skipped.length > 0
      ? 'bulk-actions__toast--warn'
      : 'bulk-actions__toast--ok';
  return (
    <div className="bulk-actions bulk-actions__result" data-testid="lead-bulk-result">
      <span className={`bulk-actions__toast ${tone}`}>{bulkRunSummary(result)}</span>
      <Button variant="ghost" size="sm" onClick={onDismiss} data-testid="lead-bulk-result-dismiss">
        Dismiss
      </Button>
      {(issues.length > 0 || result.notStarted.length > 0) && (
        <details className="bulk-actions__issues" data-testid="lead-bulk-issues">
          <summary>
            {issues.length > 0 ? `Failed and skipped (${formatCount(issues.length)})` : 'Not started'}
            {result.notStarted.length > 0 && issues.length > 0 ? `, not started (${formatCount(result.notStarted.length)})` : ''}
          </summary>
          <ul>
            {issues.map((issue) => (
              <li key={issue.borrowerId} data-outcome={issue.outcome}>
                <span className="mono">{issue.borrowerId}</span>: {issueReason(issue)}
              </li>
            ))}
            {result.notStarted.length > 0 && (
              <li data-outcome="not_started">
                {formatCount(result.notStarted.length)} not started: never sent and still selected. Run them again
                as a new run with its own rationale.
              </li>
            )}
          </ul>
        </details>
      )}
    </div>
  );
}
