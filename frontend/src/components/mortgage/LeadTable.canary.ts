/**
 * Which canary refusal the bulk toolbar may still show (W5a integrator
 * ruling R2, audit tables-07 / flow-03 carryover).
 *
 * useLeadApprovalActions keeps the row a run's canary stopped at until the
 * next run or Clear selection (that hook is not edited here). The notice
 * belongs to the run that raised it: it shows only while the selection is
 * the one that run was started with AND the open gate is that run's gate
 * (the reject gate for a reject run, the approve gate otherwise). Any
 * selection change retires it for good, even a change back.
 *
 * Plain helpers over a capture the (uncompiled) LeadTable keeps in state.
 * They ride the lazy bulk chunk (LeadBulkApproveReview re-exports them):
 * a canary exists only after a bulk run, which loaded that chunk, so the
 * shared LeadTable chunk carries none of this.
 */
import type { BulkRunCanary, BulkRunKind } from './useLeadBulkRun';

export interface CapturedCanary {
  canary: BulkRunCanary;
  /** The selection when the canary first appeared (sorted ids). */
  signature: string;
  kind: BulkRunKind;
  retired: boolean;
}

export interface BulkCanaryNoticeInput {
  canary: BulkRunCanary | null;
  selectedIds: ReadonlySet<string>;
  /** The settled run's kind (bulkRun.result?.kind), when it is known. */
  runKind: BulkRunKind | null;
  approveGateOpen: boolean;
  rejectGateOpen: boolean;
}

/** The selection as one comparable string: order-free. */
export function selectionSignature(ids: ReadonlySet<string>): string {
  return [...ids].sort().join('\n');
}

/** The capture for this render: a new canary is captured once; a selection change retires it. */
export function nextCanaryCapture(
  captured: CapturedCanary | null,
  input: BulkCanaryNoticeInput,
): CapturedCanary | null {
  const signature = selectionSignature(input.selectedIds);
  if ((captured?.canary ?? null) !== input.canary) {
    return input.canary
      ? {
          canary: input.canary,
          signature,
          kind: input.runKind ?? (input.rejectGateOpen ? 'reject' : 'approve'),
          retired: false,
        }
      : null;
  }
  if (captured && !captured.retired && captured.signature !== signature) return { ...captured, retired: true };
  return captured;
}

/**
 * One render's step, from the table's own state: the next capture (store it
 * when it changed) and the canary the toolbar shows now.
 */
export function canaryNotice(
  captured: CapturedCanary | null,
  approval: {
    bulkRunCanary: BulkRunCanary | null;
    selectedIds: ReadonlySet<string>;
    bulkRationaleOpen: boolean;
    bulkRejectOpen: boolean;
  },
  runKind: BulkRunKind | null,
): { next: CapturedCanary | null; shown: BulkRunCanary | null } {
  const input: BulkCanaryNoticeInput = {
    canary: approval.bulkRunCanary,
    selectedIds: approval.selectedIds,
    runKind,
    approveGateOpen: approval.bulkRationaleOpen,
    rejectGateOpen: approval.bulkRejectOpen,
  };
  const next = nextCanaryCapture(captured, input);
  return { next, shown: shownCanary(next, input) };
}

/** The canary the toolbar shows now, or null. */
export function shownCanary(captured: CapturedCanary | null, input: BulkCanaryNoticeInput): BulkRunCanary | null {
  if (!captured || captured.retired) return null;
  const gateOpen = captured.kind === 'reject' ? input.rejectGateOpen : input.approveGateOpen;
  return gateOpen ? captured.canary : null;
}
