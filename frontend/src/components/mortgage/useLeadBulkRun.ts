/**
 * useLeadBulkRun — the Lead Queue's bulk approve loop, with honest
 * progress and a cooperative Stop (audit tables-07, states-08 bulk half,
 * follow-up #8). Extracted from useLeadApprovalActions.bulkApprove; every
 * invariant it kept is kept here or at its caller:
 *
 *   - R5-04: a synchronous in-flight latch (a ref flipped before any await),
 *     so two clicks in one frame never start two loops.
 *   - One POST and one audit row per borrower, at most
 *     BULK_APPROVE_CONCURRENCY in flight; the caller's `decide` carries the
 *     bulk id, the shared rationale, the per-row request_id intent, a
 *     sampled draft when there is one, and the evidence / offer snapshotted
 *     when the run started.
 *   - R5-21: unmount aborts the in-flight POSTs (the server may still commit
 *     them) and stashes "N landed, rest aborted" for the next mount.
 *
 * Stop is cooperative: "Stop after this batch" sets a flag read BEFORE each
 * chunk. It never aborts a POST on the wire (an aborted approve is
 * audit-ambiguous, R5-21); the rows that never started are reported as not
 * started: never sent, safe to run again as a new run. A row that met an
 * ended session stops the run the same way and marks the partly recorded
 * run for the session dialog.
 *
 * Progress is `settled` of `total` with an ETA: the slower of the mutation
 * budget floor (remaining POSTs / MUTATION_BUDGET_PER_MINUTE; an unsampled
 * approve costs two POSTs, the draft and the approve) and the pace observed
 * after the first chunk. A polite announcement says the start, each quarter,
 * a Stop and the result; the caller renders it in an always-mounted region.
 */
import { useEffect, useRef, useState } from 'react';
import { formatCount } from '../../lib/formatters';
import { markUnrecordedWrite } from '../../lib/sessionStatus';
import { BULK_APPROVE_CONCURRENCY, MUTATION_BUDGET_PER_MINUTE } from './LeadTable.constants';
import { chunk } from './LeadTable.logic';
import { stashCancelledBulk } from './bulkApproveStash';

/** Bulk approve only in wave 3; bulk Reject (tables-07) is a wave-4 item. */
export type BulkRunKind = 'approve';
export type BulkRowOutcome = 'ok' | 'backend' | 'network' | 'session_expired' | 'aborted' | 'duplicate';

/** How one row's write ended; `message` is the server's reason for a refusal. */
export interface BulkRowReport {
  outcome: BulkRowOutcome;
  message: string | null;
}

export interface BulkRunRow {
  borrowerId: string;
  /** POSTs this row sends against the mutation budget: 2 for an unsampled approve, else 1. */
  posts: 1 | 2;
}

export interface BulkRunSpec {
  kind: BulkRunKind;
  rows: readonly BulkRunRow[];
  /** One row's write. The signal aborts only on unmount, never on Stop. */
  decide: (borrowerId: string, signal: AbortSignal) => Promise<BulkRowReport>;
}

export interface BulkRunProgress {
  kind: BulkRunKind;
  total: number;
  settled: number;
  stopRequested: boolean;
  /** Minutes left (budget floor or observed pace), or null when none is left. */
  minutesLeft: number | null;
}

export interface BulkRunIssue {
  borrowerId: string;
  outcome: Exclude<BulkRowOutcome, 'ok' | 'aborted'>;
  message: string | null;
}

export interface BulkRunResult {
  kind: BulkRunKind;
  total: number;
  ok: number;
  /** Refused, dropped or met an ended session: safe to retry, kept selected. */
  failed: BulkRunIssue[];
  /** A decision for the row was already on the wire: not a failure. */
  skipped: BulkRunIssue[];
  /** Never sent (Stop, or the session ended first): kept selected. */
  notStarted: string[];
  stopped: boolean;
  sessionEnded: boolean;
}

/** Minutes left: the slower of the budget floor and the observed pace. Null when nothing is left. */
export function bulkRunMinutesLeft(
  remaining: readonly BulkRunRow[],
  settled: number,
  elapsedMs: number,
): number | null {
  if (remaining.length === 0) return null;
  const posts = remaining.reduce((sum, row) => sum + row.posts, 0);
  const floor = posts / MUTATION_BUDGET_PER_MINUTE;
  const pace = settled > 0 ? ((elapsedMs / settled) * remaining.length) / 60_000 : 0;
  return Math.max(floor, pace);
}

export function formatMinutesLeft(minutes: number): string {
  return `about ${formatCount(Math.max(1, Math.ceil(minutes)))} min left`;
}

/** The result in one sentence (the visible summary and the announcement). */
export function bulkRunSummary(result: BulkRunResult): string {
  const parts = [`${formatCount(result.ok)} of ${formatCount(result.total)} approved`];
  if (result.failed.length > 0) parts.push(`${formatCount(result.failed.length)} failed`);
  if (result.skipped.length > 0) parts.push(`${formatCount(result.skipped.length)} skipped`);
  if (result.notStarted.length > 0) parts.push(`${formatCount(result.notStarted.length)} not started`);
  const reason = result.sessionEnded ? ' The session ended.' : result.stopped ? ' Stopped.' : '';
  return `${parts.join(', ')}.${reason}`;
}

const AUTO_DISMISS_MS = 4000;

export function useLeadBulkRun() {
  const inFlightRef = useRef(false);
  const stopRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const [progress, setProgress] = useState<BulkRunProgress | null>(null);
  const [result, setResult] = useState<BulkRunResult | null>(null);
  const [announcement, setAnnouncement] = useState('');

  /** The synchronous latch (R5-04), not the render state. */
  function isRunning(): boolean {
    return inFlightRef.current;
  }

  /** "Stop after this batch": no POST on the wire is aborted. */
  function requestStop(): void {
    if (!inFlightRef.current || stopRef.current) return;
    stopRef.current = true;
    setProgress((current) => (current ? { ...current, stopRequested: true } : current));
    setAnnouncement('Stop requested. The rows already sent will finish; the rest will not start.');
  }

  function dismissResult(): void {
    setResult(null);
  }

  /** Run the spec. Resolves null when a run was already on the wire or unmount cut it short. */
  async function start(spec: BulkRunSpec): Promise<BulkRunResult | null> {
    if (inFlightRef.current || spec.rows.length === 0) return null;
    inFlightRef.current = true;
    stopRef.current = false;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const total = spec.rows.length;
    const live = () => !ctrl.signal.aborted;
    setResult(null);
    setProgress({
      kind: spec.kind,
      total,
      settled: 0,
      stopRequested: false,
      minutesLeft: bulkRunMinutesLeft(spec.rows, 0, 0),
    });
    setAnnouncement(`Approving ${formatCount(total)} borrowers.`);

    const startedAt = Date.now();
    const groups = chunk([...spec.rows], BULK_APPROVE_CONCURRENCY);
    const failed: BulkRunIssue[] = [];
    const skipped: BulkRunIssue[] = [];
    const notStarted: string[] = [];
    let settled = 0;
    let ok = 0;
    let aborted = 0;
    let sessionEnded = false;
    let quarter = 1;

    for (let index = 0; index < groups.length; index += 1) {
      const group = groups[index];
      if (ctrl.signal.aborted) {
        aborted += group.length;
        continue;
      }
      // Cooperative Stop and an ended session: checked before each chunk only.
      if (stopRef.current || sessionEnded) {
        notStarted.push(...group.map((row) => row.borrowerId));
        continue;
      }
      const reports = await Promise.all(group.map((row) => spec.decide(row.borrowerId, ctrl.signal).then(
        (report) => report,
        (error: unknown): BulkRowReport => ({
          outcome: 'backend',
          message: error instanceof Error ? error.message : null,
        }),
      ).then((report) => {
        settled += 1;
        const count = settled;
        if (live()) setProgress((current) => (current ? { ...current, settled: count } : current));
        return report;
      })));
      reports.forEach((report, position) => {
        const borrowerId = group[position].borrowerId;
        if (report.outcome === 'ok') ok += 1;
        else if (report.outcome === 'aborted') aborted += 1;
        else if (report.outcome === 'duplicate') skipped.push({ borrowerId, outcome: 'duplicate', message: report.message });
        else failed.push({ borrowerId, outcome: report.outcome, message: report.message });
        if (report.outcome === 'session_expired') sessionEnded = true;
      });
      if (!live()) continue;
      const remaining = groups.slice(index + 1).flat();
      const minutesLeft = bulkRunMinutesLeft(remaining, settled, Date.now() - startedAt);
      setProgress((current) => (current ? { ...current, settled, minutesLeft } : current));
      let crossed = 0;
      while (quarter < 4 && settled * 4 >= total * quarter) {
        crossed = quarter;
        quarter += 1;
      }
      if (crossed > 0 && remaining.length > 0 && !stopRef.current && !sessionEnded) {
        setAnnouncement(`${crossed * 25}% done: ${formatCount(settled)} of ${formatCount(total)}.`);
      }
    }

    inFlightRef.current = false;
    abortRef.current = null;
    if (ctrl.signal.aborted) {
      // R5-21, unchanged: the next mount flashes what landed and what was cut.
      stashCancelledBulk(ok, aborted);
      stopRef.current = false;
      return null;
    }
    if (sessionEnded) markUnrecordedWrite('bulk_approval');
    const final: BulkRunResult = {
      kind: spec.kind,
      total,
      ok,
      failed,
      skipped,
      notStarted,
      // Only a Stop that left rows unsent cut the run short.
      stopped: stopRef.current && notStarted.length > 0,
      sessionEnded,
    };
    stopRef.current = false;
    setProgress(null);
    setResult(final);
    setAnnouncement(bulkRunSummary(final));
    return final;
  }

  // Unmount aborts the in-flight POSTs (R5-21); the loop stashes the result.
  useEffect(() => () => abortRef.current?.abort(), []);

  // A clean result clears itself; one with failures or unstarted rows stays
  // until it is dismissed.
  useEffect(() => {
    if (!result || result.failed.length > 0 || result.notStarted.length > 0) return undefined;
    const timer = window.setTimeout(() => setResult(null), AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [result]);

  return {
    progress,
    result,
    announcement,
    isRunning,
    start,
    requestStop,
    dismissResult,
  };
}
