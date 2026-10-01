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
 */
import { useState } from 'react';
import type { BulkRunCanary, BulkRunKind } from './useLeadBulkRun';

interface CapturedCanary {
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

/** The canary the toolbar shows now, or null. */
export function useBulkCanaryNotice(input: BulkCanaryNoticeInput): BulkRunCanary | null {
  const signature = selectionSignature(input.selectedIds);
  const [captured, setCaptured] = useState<CapturedCanary | null>(null);
  let current = captured;
  if ((current?.canary ?? null) !== input.canary) {
    // A new canary (or none): capture the run it belongs to, once.
    current = input.canary
      ? {
          canary: input.canary,
          signature,
          kind: input.runKind ?? (input.rejectGateOpen ? 'reject' : 'approve'),
          retired: false,
        }
      : null;
    setCaptured(current);
  } else if (current && !current.retired && current.signature !== signature) {
    current = { ...current, retired: true };
    setCaptured(current);
  }
  if (!current || current.retired) return null;
  const gateOpen = current.kind === 'reject' ? input.rejectGateOpen : input.approveGateOpen;
  return gateOpen ? current.canary : null;
}
