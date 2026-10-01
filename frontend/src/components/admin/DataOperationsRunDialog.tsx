import { useId, useRef, useState } from 'react';
import { useModalDialog } from '../../hooks/useModalDialog';
import { Icon } from '../Icon';
import './DataOperationsRunDialog.css';

/**
 * "Run <job>?" — the confirm step in front of a governed data refresh
 * (2026-09-21 audit critic-09). Run used to start a production job in one
 * click, with the server's confirm and reason fields filled in for the
 * operator; a gold refresh rewrites the scores every user reads.
 *
 * A native modal `<dialog>` through `useModalDialog` (showModal, the Tab wrap
 * and the shared Escape stack; the pattern of feedback/UnsavedChangesDialog):
 * focus opens on Cancel, Escape is Cancel, and closing returns focus to the
 * Run button that opened it. It names the job, its last run and the
 * downstream effect, and asks for one of the four reasons the server accepts
 * (backend/schemas/admin.py, a closed Literal) with none pre-chosen; only
 * Start posts. Pessimistic: while the start is in flight both buttons are
 * disabled and the dialog cannot be dismissed; a failure shows inside the
 * dialog and keeps it open.
 *
 * DEPARTURE: the prototype has no confirm dialog; this is built from its
 * `.approval` banner (design_files/index.html:634-651) inside a `.surface`
 * dialog shell (deviation:admin-run-confirm).
 */

export type OperationJobKey = 'fred_rates' | 'silver_refresh' | 'gold_refresh' | 'lifecycle_sync';

export type OperationRunReason = 'operator_refresh' | 'source_update' | 'release_validation' | 'support_triage';

/** The server's closed reason vocabulary, in the order the dialog offers it. */
export const OPERATION_RUN_REASONS: ReadonlyArray<{ value: OperationRunReason; label: string }> = [
  { value: 'operator_refresh', label: 'Routine operator refresh' },
  { value: 'source_update', label: 'Source data was updated' },
  { value: 'release_validation', label: 'Release validation' },
  { value: 'support_triage', label: 'Support triage' },
];

/** What each job changes downstream, in the operator's words. */
export const OPERATION_EFFECTS: Readonly<Record<OperationJobKey, string>> = {
  fred_rates:
    'Updates the weekly 30-year rate in silver; rate spreads and In the Money results change after the next gold refresh.',
  silver_refresh:
    'Rebuilds the silver feature tables; scores and segments change only after the next gold refresh.',
  gold_refresh:
    'Rewrites Borrower 360, lead scores, segment populations and source readiness that every user reads.',
  lifecycle_sync:
    'Mirrors Lakebase approval and outreach state into the gold lifecycle tables that dashboards read.',
};

export interface RunDialogJob {
  key: OperationJobKey;
  label: string;
  job_name: string;
}

interface DataOperationsRunDialogProps {
  job: RunDialogJob;
  /** The job's last run, already formatted (`Run 201 · <time> · success`). */
  lastRun: string;
  starting: boolean;
  error: string | null;
  onConfirm: (reason: OperationRunReason) => void;
  onCancel: () => void;
}

export function DataOperationsRunDialog({ job, lastRun, starting, error, onConfirm, onCancel }: DataOperationsRunDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const effectId = useId();
  const reasonName = useId();
  const [reason, setReason] = useState<OperationRunReason | null>(null);

  const dismiss = () => {
    if (!starting) onCancel();
  };
  useModalDialog({ open: true, dialogRef, initialFocusRef: cancelRef, onDismiss: dismiss, backdrop: false });

  return (
    <dialog
      ref={dialogRef}
      className="surface surface--elev admin-run-dialog"
      aria-labelledby={titleId}
      aria-describedby={effectId}
    >
      <div className="approval">
        <div className="approval__ico" aria-hidden="true">
          <Icon name="play" size={16} />
        </div>
        <div className="approval__body">
          <div className="approval__title" id={titleId}>Run {job.label}?</div>
          <div className="approval__sub">Starts the Databricks job now. The start is recorded in the audit ledger.</div>
        </div>
      </div>
      <dl className="admin-run-dialog__facts">
        <dt>Job</dt>
        <dd>
          {job.label} <span className="mono muted">{job.job_name}</span>
        </dd>
        <dt>Last run</dt>
        <dd>{lastRun}</dd>
        <dt>Effect</dt>
        <dd id={effectId}>{OPERATION_EFFECTS[job.key]}</dd>
      </dl>
      <fieldset className="admin-run-dialog__reasons" disabled={starting}>
        <legend>Reason (required)</legend>
        {OPERATION_RUN_REASONS.map((option) => (
          <label key={option.value} className="admin-run-dialog__reason">
            <input
              type="radio"
              name={reasonName}
              value={option.value}
              checked={reason === option.value}
              onChange={() => setReason(option.value)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>
      {error && (
        <div className="status-callout status-callout--danger" role="alert">
          {error}
        </div>
      )}
      <div className="admin-run-dialog__actions">
        <button ref={cancelRef} type="button" className="btn btn--ghost btn--sm" onClick={dismiss} disabled={starting}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn--primary btn--sm"
          onClick={() => {
            if (reason && !starting) onConfirm(reason);
          }}
          disabled={reason === null || starting}
        >
          {starting ? 'Starting…' : `Start ${job.label}`}
        </button>
      </div>
    </dialog>
  );
}
