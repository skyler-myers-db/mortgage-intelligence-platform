import { useEffect, useId, useRef, useState } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useModalDialog } from '../../hooks/useModalDialog';
import type { DistributionOfficerLoad, DistributionPreview } from '../../lib/apiClients/sales';
import { ApiError } from '../../lib/apiTransport';
import { formatCount } from '../../lib/formatters';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import { useCommitDistribution, useDistributionPreview } from '../../lib/mutations/salesLoop';
import { loanOfficersOnly, useSalesRoster } from '../../lib/salesRoster';
import { Button, Chip } from '../Primitives';
import { DescribedErrorBody } from '../ui/DescribedError';
import { isCountdown429, toastDistributed, toastSalesWriteFailure, withRetry } from './salesUndoToasts';
import './DistributionBoard.css';

/**
 * The Distribution Board (2026-09-21 audit wow-power-5 step 2): a manager
 * previews a capacity-aware fill of approved leads across the loan officers,
 * then commits exactly the plan they saw.
 *
 * On open it runs ONE dry run (POST /sales/distribute, strategy
 * 'score_balanced', dry_run: true): audit-free, it writes nothing and returns
 * per-officer aggregates plus a plan fingerprint, never a borrower id. Commit
 * sends the fingerprint and the intent's request id; the server re-plans on
 * live counts and answers 409 `distribution_plan_changed` (writing nothing)
 * when the plan moved, so the Board re-previews, shows before -> after per
 * officer and commits only on the new fingerprint with a NEW request id. A
 * 429 keeps a Retry behind its countdown; any other failure stays here.
 *
 * A native modal <dialog> through useModalDialog (DataOperationsRunDialog's
 * pattern): focus opens on Cancel, Escape is Cancel, focus returns to the
 * opener, and the dialog cannot be dismissed while committing. Pessimistic:
 * the success toast (with the run's Undo) is raised only after the 200.
 *
 * deviation:distribution-board. The prototype has no distribution surface:
 * the head is its `.approval` banner (design_files/index.html:634-651), the
 * body its `.tbl` (:595) and the per-officer load bar is drawn from the
 * `.conf__bars` vocabulary (:560-565) as one aria-hidden CSS grid.
 */

/** The server's `lo_emails` cap (DistributeLeadsRequest, max_length=25). */
export const BOARD_MAX_OFFICERS = 25;

export interface DistributionBoardProps {
  /** The pool; omitted = the server's approved, eligible list. */
  borrowerIds?: string[];
  onClose: () => void;
}

const COMMIT_FAILED = "Couldn't distribute the leads";
const PREVIEW_FAILED = "Couldn't preview the distribution";

function refreshSalesViews(queryClient: QueryClient): void {
  // Only the audit-free manager reads refetch; the queue reads stay stale-marked.
  void queryClient.invalidateQueries({ queryKey: ['mip', 'sales', 'ops-snapshot'], refetchType: 'active' });
  void queryClient.invalidateQueries({ queryKey: ['mip', 'sales', 'workload'], refetchType: 'active' });
}

function summaryLine(preview: DistributionPreview): string {
  const parts = [`${formatCount(preview.placed_count)} of ${formatCount(preview.pool_count)} leads placed`];
  if (preview.unplaced_no_capacity > 0) parts.push(`${formatCount(preview.unplaced_no_capacity)} left unplaced: no capacity`);
  if (preview.skipped_already_assigned > 0) parts.push(`${formatCount(preview.skipped_already_assigned)} skipped: already assigned`);
  if (preview.skipped_not_assignable > 0) {
    parts.push(`${formatCount(preview.skipped_not_assignable)} skipped: not approved or not contactable`);
  }
  return parts.join(' · ');
}

function LoadBar({ officer }: { officer: DistributionOfficerLoad }) {
  const free = Math.max(0, officer.capacity_per_day - officer.open_count - officer.added_count);
  const over = officer.open_count > officer.capacity_per_day;
  return (
    <div
      className="distribution-board__bar"
      aria-hidden="true"
      style={{ gridTemplateColumns: `${officer.open_count}fr ${officer.added_count}fr ${free}fr` }}
    >
      <span className="distribution-board__seg distribution-board__seg--open" />
      <span className="distribution-board__seg distribution-board__seg--added" />
      <span className="distribution-board__seg distribution-board__seg--free" />
      {over && <span className="distribution-board__over" />}
    </div>
  );
}

function ChangedNotice({ before, after }: { before: DistributionPreview; after: DistributionPreview }) {
  const previous = new Map(before.officers.map((officer) => [officer.email, officer.added_count]));
  return (
    <div className="approval distribution-board__changed" role="status">
      <div className="approval__body">
        <div className="approval__title">The plan changed since your preview</div>
        <ul className="approval__sub distribution-board__changes">
          {after.officers.map((officer) => (
            <li key={officer.email}>
              {officer.display_label}: +{formatCount(previous.get(officer.email) ?? 0)} → +{formatCount(officer.added_count)}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function DistributionBoard({ borrowerIds, onClose }: DistributionBoardProps) {
  const queryClient = useQueryClient();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const commitRef = useRef<HTMLButtonElement | null>(null);
  const failureRef = useRef<HTMLDivElement | null>(null);
  const previewStarted = useRef(false);
  const titleId = useId();
  const subId = useId();
  const requestIds = useIntentRequestIds();
  const roster = useSalesRoster(loanOfficersOnly);
  const previewWrite = useDistributionPreview(queryClient);
  const commitWrite = useCommitDistribution(queryClient);
  // The plan on screen: kept while a re-preview runs, so Commit keeps its
  // focus (aria-busy, never native disabled) instead of dropping it to <body>.
  const [preview, setPreview] = useState<DistributionPreview | null>(null);
  const [before, setBefore] = useState<DistributionPreview | null>(null);
  // The server refused the plan on screen as moved (409) and no new plan has
  // arrived yet: its fingerprint must never be committed again.
  const [stale, setStale] = useState(false);
  // A non-429 preview failure, kept until the next preview settles so the
  // callout (and its Try again, which may hold focus) stays put meanwhile.
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [commitError, setCommitError] = useState<unknown>(null);

  const officers = [...(roster.data ?? [])].sort((a, b) => a.display_label.localeCompare(b.display_label));
  const planned = officers.slice(0, BOARD_MAX_OFFICERS);
  const plan = { borrowerIds: borrowerIds ?? [], loEmails: planned.map((officer) => officer.email) };
  const previewing = previewWrite.isPending;
  const committing = commitWrite.isPending;

  // The failure callout is about to go: a Try again inside it that holds
  // focus hands it to Commit (focusable even while aria-disabled).
  const releaseFailureFocus = (): void => {
    if (failureRef.current?.contains(document.activeElement)) commitRef.current?.focus();
  };

  const runPreview = (retry: () => void): void => {
    previewWrite.mutateAsync(plan).then(
      (result) => {
        releaseFailureFocus();
        setPreview(result.preview ?? null);
        setStale(false);
        setPreviewError(null);
      },
      (error: unknown) => {
        if (!isCountdown429(error)) {
          setPreviewError(error);
          return;
        }
        releaseFailureFocus();
        setPreviewError(null);
        toastSalesWriteFailure(PREVIEW_FAILED, error, 'the preview', retry);
      },
    );
  };

  useEffect(() => {
    if (previewStarted.current || plan.loEmails.length === 0) return;
    previewStarted.current = true;
    withRetry(runPreview);
  });

  const dismiss = () => {
    if (!committing) onClose();
  };
  useModalDialog({ open: true, dialogRef, initialFocusRef: cancelRef, onDismiss: dismiss, backdrop: false });

  const runCommit = (current: DistributionPreview, retry: () => void): void => {
    const intent = intentFingerprint('board-commit', current.plan_fingerprint, plan.loEmails.join(','));
    const requestId = requestIds.idFor(intent);
    setCommitError(null);
    commitWrite.mutateAsync({ ...plan, fingerprint: current.plan_fingerprint, requestId }).then(
      (result) => {
        requestIds.settle(intent);
        onClose();
        toastDistributed(
          {
            queryClient,
            requestIdFor: (fingerprint) => requestIds.idFor(fingerprint),
            settle: (fingerprint) => requestIds.settle(fingerprint),
            onReverted: () => refreshSalesViews(queryClient),
          },
          { assigned_count: result.assigned_count, assignments: result.assignments, audit_event_id: result.audit_event_id ?? null },
          requestId,
        );
        refreshSalesViews(queryClient);
      },
      (error: unknown) => {
        // The commit's only 409 is the moved plan (backend/api/sales.py). A
        // transport that does not yet carry a 409's top-level `reason` reads
        // null (INTEGRATOR: W5d w5-actor-binding adds that parse, through
        // apiActorBinding.actorRefusalReason).
        if (
          error instanceof ApiError
          && error.status === 409
          && (error.reason === 'distribution_plan_changed' || error.reason === null)
        ) {
          setBefore(current);
          setStale(true);
          withRetry(runPreview);
          return;
        }
        if (isCountdown429(error)) {
          toastSalesWriteFailure(COMMIT_FAILED, error, 'the distribution', retry);
          return;
        }
        setCommitError(error);
      },
    );
  };

  // Commit is aria-disabled, never native disabled: a re-preview that places
  // nothing (or fails) must not drop a focused Commit to <body>.
  const canCommit = preview !== null && preview.placed_count > 0 && !stale;
  const commit = () => {
    if (!preview || !canCommit || committing || previewing) return;
    withRetry((retry) => runCommit(preview, retry));
  };

  const capped = officers.length > BOARD_MAX_OFFICERS;
  const loading = roster.isPending || (previewing && preview === null);
  // A 429 is the toast's countdown, not a failure shown here.
  const failure = roster.error ?? previewError;
  const noOfficers = roster.isSuccess && planned.length === 0;

  return (
    <dialog
      ref={dialogRef}
      className="surface surface--elev distribution-board"
      aria-labelledby={titleId}
      aria-describedby={subId}
    >
      <div className="approval">
        <div className="approval__body">
          <div className="approval__title" id={titleId}>Distribute approved leads</div>
          <div className="approval__sub" id={subId}>
            A capacity-aware preview: highest scores first, each lead to the loan officer with the most room,
            in-region first. Nothing is assigned until you commit.
          </div>
        </div>
      </div>
      {capped && (
        <div className="status-callout status-callout--warning" role="note">
          A distribution plans across at most {BOARD_MAX_OFFICERS} loan officers: this preview uses the first{' '}
          {BOARD_MAX_OFFICERS} of {formatCount(officers.length)} by name.
        </div>
      )}
      {before && preview && !previewing && !stale && <ChangedNotice before={before} after={preview} />}
      {noOfficers && (
        <div className="status-callout status-callout--warning" role="note">
          No active loan officers are in your scope, so there is nothing to plan.
        </div>
      )}
      <div className="tbl-wrap distribution-board__wrap" aria-busy={loading}>
        <table className="tbl distribution-board__table">
          <caption className="sr-only">Planned new leads per loan officer</caption>
          <thead>
            <tr>
              <th scope="col">Loan officer</th>
              <th scope="col">Region</th>
              <th scope="col" className="num">Open</th>
              <th scope="col" className="num">+New</th>
              <th scope="col" className="num">Capacity/day</th>
              <th scope="col" className="num">Σ score added</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? null
              : (preview?.officers ?? []).map((officer) => (
                <tr key={officer.email}>
                  <td>
                    <div className="distribution-board__name">{officer.display_label}</div>
                    <LoadBar officer={officer} />
                  </td>
                  <td><Chip variant="neutral">{officer.region || 'Generalist'}</Chip></td>
                  <td className="num mono">{formatCount(officer.open_count)}</td>
                  <td className="num mono">+{formatCount(officer.added_count)}</td>
                  <td className="num mono">{formatCount(officer.capacity_per_day)}</td>
                  <td className="num mono">{formatCount(officer.added_score_sum)}</td>
                </tr>
              ))}
          </tbody>
        </table>
        {loading && (
          <div className="distribution-board__loading">
            <span className="sr-only">Planning the distribution…</span>
            {[0, 1, 2].map((row) => (
              <span key={row} className="skeleton kpi__delta-skeleton" aria-hidden="true" />
            ))}
          </div>
        )}
      </div>
      {failure ? (
        <div ref={failureRef} className="status-callout status-callout--danger distribution-board__failure" role="alert">
          <span>
            {PREVIEW_FAILED}: <DescribedErrorBody error={failure} subject="the distribution preview" />
          </span>
          {roster.error ? null : (
            <Button size="sm" loading={previewing} onClick={() => withRetry(runPreview)}>
              Try again
            </Button>
          )}
        </div>
      ) : null}
      {preview && (
        <div className="distribution-board__summary">
          <div className="fs-13">{summaryLine(preview)}</div>
          <div className="muted fs-12">
            Highest score first · capacity is the daily capacity minus the open book · an officer whose region is the
            property state first, then the least-loaded officer, then the least score added.
          </div>
        </div>
      )}
      {commitError ? (
        <div className="status-callout status-callout--danger" role="alert">
          {COMMIT_FAILED}: <DescribedErrorBody error={commitError} subject="the distribution" />
        </div>
      ) : null}
      <div className="distribution-board__actions">
        <button ref={cancelRef} type="button" className="btn btn--ghost btn--sm" onClick={dismiss} disabled={committing}>
          Cancel
        </button>
        <Button
          ref={commitRef}
          variant="primary"
          size="sm"
          onClick={commit}
          loading={committing || (previewing && preview !== null)}
          aria-disabled={canCommit ? undefined : true}
        >
          Commit distribution
        </Button>
      </div>
    </dialog>
  );
}
