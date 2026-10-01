/**
 * Bulk approve and bulk Reject runs (audit tables-07, flow-03 / states-06,
 * D-approval-flow-a1 / -d), moved out of useLeadApprovalActions so they
 * ride the lazy bulk chunk (re-exported from LeadBulkApproveReview.tsx)
 * with the gates they start from. The hook hands each run its state and
 * setters through BulkDecisionContext, built synchronously at the call, so
 * the R5-04 latch is still read before any await; without the chunk the
 * hook fails closed (nothing is drafted or sent).
 */
import type { RefObject } from 'react';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { _newBulkId } from './LeadTable.logic';
import type { RejectReasonCode } from './LeadTable.types';
import { CONSENT_REJECT_CODES } from './LeadTable.constants';
import { bulkSampleCoverage, coveredOfferCodes } from './LeadBulkApproveReview.coverage';
import { approveWithReport, type DecisionSnapshot, type DecisionWriteDeps } from './leadDecisionWrites';
import { rejectWithReport } from './leadBulkRejectWrite';
import { runBulk } from './leadBulkRunLoop';
import type { BulkRunCanary, BulkRunEngine, BulkRunResult } from './useLeadBulkRun';

/** The gate's Preview control: where a refused run sends focus. */
const PREVIEW_SAMPLES_SELECTOR = '[data-testid="lead-bulk-preview-samples"]';

/** What a run reads and sets in useLeadApprovalActions, read when the run is called. */
export interface BulkDecisionContext {
  /** useLeadBulkRun's state, which runBulk writes. */
  engine: BulkRunEngine;
  /** A run is on the wire (the R5-04 latch or the render state). */
  busy: boolean;
  /** The selected rows the run would decide: eligible ones only. */
  ids: string[];
  leadsById: ReadonlyMap<string, LeadSummary>;
  writeDeps: DecisionWriteDeps;
  /** The approver / campaign-binding gate for this run's kind. */
  canStart: () => boolean;
  /** Open or close this run's gate (approve: rationale; reject: reason + note). */
  openGate: (open: boolean) => void;
  /** Open the approve gate with focus on its shared rationale. */
  openBulkRationale: () => void;
  approveBtnRef: RefObject<HTMLButtonElement | null>;
  /** The run's rows are on the wire (they lock, and the last run's lines clear). */
  begin: (ids: readonly string[]) => void;
  end: () => void;
  /** After a run: what stays selected, one invalidation, where focus goes. */
  settle: (result: BulkRunResult) => void;
  setCanary: (canary: BulkRunCanary | null) => void;
}

/** What each row of a run certifies, read once when the run starts. */
function snapshotRows(leadsById: ReadonlyMap<string, LeadSummary>, ids: readonly string[]): Map<string, DecisionSnapshot> {
  return new Map(ids.map((borrowerId) => {
    const lead = leadsById.get(borrowerId);
    return [borrowerId, {
      evidenceIds: [...(lead?.evidence_ids ?? [])],
      offerCode: lead?.recommended_offer_code ?? null,
    }];
  }));
}

/**
 * A run that settled: the selection and focus settle, and the gate stays
 * open (with its inputs) after a canary stop, saying which row and why;
 * otherwise it closes. null: unmount cut the run short (stashed, R5-21) or
 * one was running.
 */
function finish(ctx: BulkDecisionContext, result: BulkRunResult | null): boolean {
  ctx.end();
  if (!result) return false;
  ctx.settle(result);
  if (result.canary) {
    // The first row was refused: nothing else was sent.
    ctx.openGate(true);
    ctx.setCanary(result.canary);
    return false;
  }
  ctx.openGate(false);
  return true;
}

/**
 * Bulk Reject (audit tables-07, D-approval-flow-d): one reject POST (one
 * OUTREACH_REJECT row) per selected eligible row under one bulk id, one
 * reason and one required shared note, through the same run as a bulk
 * approve (canary, BULK_APPROVE_CONCURRENCY, Stop, R5-21). Two or more
 * rows only (one row goes to its own reject panel); a consent reason (Do
 * Not Call, Opt-out) is never applied in bulk. The server enforces both.
 *
 * @returns true once a run settled: the gate then closes.
 */
export async function runBulkReject(ctx: BulkDecisionContext, reasonCode: RejectReasonCode, note: string): Promise<boolean> {
  if (ctx.busy) return false;
  const { ids } = ctx;
  const sharedNote = note.trim();
  if (ids.length < 2 || sharedNote.length === 0 || CONSENT_REJECT_CODES.includes(reasonCode)) return false;
  if (!ctx.canStart()) return false;
  const bulkId = _newBulkId();
  const snapshots = snapshotRows(ctx.leadsById, ids);
  ctx.begin(ids);
  const result = await runBulk(ctx.engine, {
    kind: 'reject',
    rows: ids.map((borrowerId) => ({ borrowerId, posts: 1 })),
    decide: (borrowerId, signal) => rejectWithReport(ctx.writeDeps, borrowerId, signal, {
      bulkId, reasonCode, note: sharedNote, snapshot: snapshots.get(borrowerId),
    }),
  });
  return finish(ctx, result);
}

/**
 * Bulk-approve: one approve POST (one audit row) per selected eligible
 * row, BULK_APPROVE_CONCURRENCY at a time, through useLeadBulkRun. We
 * deliberately do NOT invent a server-side bulk endpoint: every approval
 * keeps its own governed draft proof and audit row.
 *
 * A run is two or more rows (one row goes to its own review), always has
 * a bulk id and a shared rationale, and starts only once every offer in
 * it has a previewed sample: otherwise the gate reopens with focus on
 * its Preview control and nothing is drafted or sent.
 *
 * @param sampleDrafts drafts the approver previewed through the gate's
 *   "Preview ... sample drafts": those rows are approved with exactly that
 *   copy (review_mode 'bulk_sample'), so what was shown is what their audit
 *   rows certify; the rest are drafted in the run and approved under the
 *   shared rationale (review_mode 'bulk_cohort'), and only for an offer the
 *   samples showed.
 * @param rationale the gate's shared rationale (the toolbar's own state).
 * @returns true once a run settled: the toolbar then clears its rationale.
 */
export async function runBulkApprove(
  ctx: BulkDecisionContext,
  sampleDrafts: ReadonlyMap<string, OutreachDraftResult> | undefined,
  rationale: string,
): Promise<boolean> {
  if (ctx.busy) return false;
  const { ids } = ctx;
  // One row is never a bulk run: it goes to its own review (the toolbar
  // and the keymap route it there); nothing is drafted or sent here.
  if (ids.length < 2) return false;
  if (!ctx.canStart()) return false;
  const drafts = sampleDrafts ?? new Map<string, OutreachDraftResult>();
  const sharedRationale = rationale.trim();
  if (sharedRationale.length === 0) {
    ctx.openBulkRationale();
    return false;
  }
  const runRows = ids.map((id) => ctx.leadsById.get(id) ?? { borrower_id: id });
  if (!bulkSampleCoverage(runRows, drafts).complete) {
    ctx.openGate(true);
    requestAnimationFrame(() => {
      const toolbar = ctx.approveBtnRef.current?.closest('[data-testid="lead-bulk-actions"]') ?? document;
      toolbar.querySelector<HTMLElement>(PREVIEW_SAMPLES_SELECTOR)?.focus();
    });
    return false;
  }
  const bulkId = _newBulkId();
  const covered = coveredOfferCodes(runRows, drafts);
  const snapshots = snapshotRows(ctx.leadsById, ids);
  ctx.begin(ids);
  const result = await runBulk(ctx.engine, {
    kind: 'approve',
    rows: ids.map((borrowerId) => ({ borrowerId, posts: drafts.has(borrowerId) ? 1 : 2 })),
    decide: (borrowerId, signal) => approveWithReport(ctx.writeDeps, borrowerId, signal, {
      bulk_id: bulkId,
      bulk_rationale: sharedRationale,
      suppressInvalidation: true,
      snapshot: snapshots.get(borrowerId),
      coveredOfferCodes: covered,
    }, drafts.get(borrowerId) ?? null),
  });
  return finish(ctx, result);
}
