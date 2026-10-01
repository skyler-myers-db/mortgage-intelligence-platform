/**
 * The bulk run loop (audit tables-07, states-08 bulk half, follow-up #8;
 * D-approval-flow-a1 / -d canary), moved out of useLeadBulkRun so it rides
 * the lazy bulk chunk (re-exported from LeadBulkApproveReview.tsx): a run
 * starts only from a bulk gate, which is in that chunk, so the loop is
 * loaded whenever a run can start. leadBulkDecisions starts it, and
 * useLeadApprovalActions reads those from the chunk's module cache,
 * synchronously (the R5-04 latch is still flipped before any await); with
 * the chunk gone, bulk decisions fail closed. The invariants are
 * useLeadBulkRun's, unchanged (see its header).
 */
import { formatCount } from '../../lib/formatters';
import { markUnrecordedWrite } from '../../lib/sessionStatus';
import { BULK_APPROVE_CONCURRENCY, MUTATION_BUDGET_PER_MINUTE } from './LeadTable.constants';
import { stashCancelledBulk } from './bulkApproveStash';
import {
  bulkRunSummary,
  bulkRunVerb,
  type BulkRowReport,
  type BulkRunCanary,
  type BulkRunEngine,
  type BulkRunIssue,
  type BulkRunResult,
  type BulkRunRow,
  type BulkRunSpec,
} from './useLeadBulkRun';

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

/** Run the spec. Resolves null when a run was already on the wire or unmount cut it short. */
export async function runBulk(engine: BulkRunEngine, spec: BulkRunSpec): Promise<BulkRunResult | null> {
  const { inFlightRef, stopRef, abortRef, setProgress, setResult, setAnnouncement } = engine;
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
  setAnnouncement(`${bulkRunVerb(spec.kind)} ${formatCount(total)} borrowers.`);

  const startedAt = Date.now();
  const queue = [...spec.rows];
  const failed: BulkRunIssue[] = [];
  const skipped: BulkRunIssue[] = [];
  const notStarted: string[] = [];
  let settled = 0;
  let ok = 0;
  let aborted = 0;
  let sessionEnded = false;
  let quarter = 1;
  // Canary: one row at a time until one comes back ok.
  let fannedOut = false;
  let canary: BulkRunCanary | null = null;

  while (queue.length > 0) {
    const group = queue.splice(0, fannedOut ? BULK_APPROVE_CONCURRENCY : 1);
    if (ctrl.signal.aborted) {
      aborted += group.length;
      continue;
    }
    // Cooperative Stop, an ended session and a canary refusal: checked
    // before each chunk only.
    if (stopRef.current || sessionEnded || canary) {
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
      if (fannedOut || report.outcome === 'aborted' || report.outcome === 'duplicate') return;
      if (report.outcome === 'ok') fannedOut = true;
      else canary = { borrowerId, message: report.message };
    });
    if (!live()) continue;
    const remaining = queue;
    const minutesLeft = bulkRunMinutesLeft(remaining, settled, Date.now() - startedAt);
    setProgress((current) => (current ? { ...current, settled, minutesLeft } : current));
    let crossed = 0;
    while (quarter < 4 && settled * 4 >= total * quarter) {
      crossed = quarter;
      quarter += 1;
    }
    if (crossed > 0 && remaining.length > 0 && !stopRef.current && !sessionEnded && !canary) {
      setAnnouncement(`${crossed * 25}% done: ${formatCount(settled)} of ${formatCount(total)}.`);
    }
  }

  inFlightRef.current = false;
  abortRef.current = null;
  if (ctrl.signal.aborted) {
    // R5-21, unchanged: the next mount flashes what landed and what was cut.
    stashCancelledBulk(ok, aborted, spec.kind);
    stopRef.current = false;
    return null;
  }
  if (sessionEnded) markUnrecordedWrite(spec.kind === 'reject' ? 'bulk_rejection' : 'bulk_approval');
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
    canary,
  };
  stopRef.current = false;
  setProgress(null);
  setResult(final);
  setAnnouncement(bulkRunSummary(final));
  return final;
}
