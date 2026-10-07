/**
 * useLeadSalesActions — the sales-operations half of the ranked-borrower
 * table: optimistic per-row overrides from assignment / lifecycle writes (and
 * therefore the merged `displayLeads` view every other consumer reads), assign
 * + round-robin distribution, and which row's call-disposition panel is open.
 * The panel's fields and their pre-flight validation are the panel's own
 * state (LeadDispositionPanel; audit runtime-04 slice 2), so typing a note
 * never re-renders the table. Extracted from LeadTable.tsx (file-size gate,
 * plan item 2).
 *
 * The writes run on the sales mutations (lib/mutations/sales, audit stack-09
 * / wow-power-5 step 1): networkMode 'always', one request_id per intent,
 * and an honest strategy ('manual' for one loan officer, 'round_robin' for
 * two or more; 'score_balanced' is sent only by the Distribution Board).
 * Results and write failures go to the shell toast region with the audit
 * event id; an assign or distribution toast carries Undo (salesUndoToasts,
 * states-07 item 1) and a 429 keeps a Retry behind its countdown (states-08).
 */

import { useRef, useState } from 'react';
import { useIsMutating, type QueryClient } from '@tanstack/react-query';
import type { LeadSummary, SalesTeamMember } from '../../types';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import {
  isSingleAssignment,
  salesMutationKeys,
  useAssignLeads,
  useLogDisposition,
  type LogDispositionVariables,
} from '../../lib/mutations/sales';
import { toast } from '../../lib/toast';
import { dispositionLabel } from './LeadTable.logic';
import { toastWriteFailure, toastWriteRefusal } from './leadWriteFailureToast';
import {
  toastAssigned,
  toastDistributed,
  toastSalesWriteFailure,
  withRetry,
  type SalesUndoContext,
} from './salesUndoToasts';

/** One assignment row as returned by `assignLead` / `distributeLeads`. */
export interface LeadAssignmentResult {
  borrower_id: string;
  assigned_to_email: string;
  assigned_to_label?: string | null;
  assigned_at: string;
  expires_at?: string | null;
  status?: LeadSummary['assignment_status'];
  assignment_id?: string | null;
}

const ASSIGN_FAILED = "Couldn't assign the selected leads";

/** What the disposition panel submits: the POST body, validated by the panel. */
export type LeadDispositionPayload = LogDispositionVariables['payload'];

export interface UseLeadSalesActionsInput {
  leads: LeadSummary[];
  salesTeam: SalesTeamMember[];
  queryClient: QueryClient;
}

export function useLeadSalesActions({
  leads,
  salesTeam,
  queryClient,
}: UseLeadSalesActionsInput) {
  const [selectedAssignee, setSelectedAssignee] = useState<string>('');
  const [salesOverrides, setSalesOverrides] = useState<Record<string, Partial<LeadSummary>>>({});
  const [pendingDisposition, setPendingDisposition] = useState<string | null>(null);
  // The loan officer the panel opens with (the row's assignee, else the
  // toolbar's); the panel owns every field after that.
  const [dispositionInitialLo, setDispositionInitialLo] = useState('');
  const assignLeads = useAssignLeads(queryClient);
  const logDisposition = useLogDisposition(queryClient);
  const requestIds = useIntentRequestIds();
  // Synchronous latch for assign AND disposition: two clicks in one frame
  // both see the render's `salesBusy=false`, and a disposition had no guard
  // at all, so a double click logged two.
  const salesInFlightRef = useRef(false);
  const salesBusy = useIsMutating({ mutationKey: salesMutationKeys.all }, queryClient) > 0;
  // The select shows the first loan officer until the user picks one.
  const effectiveAssignee = selectedAssignee || salesTeam[0]?.email || '';

  // The overrides ARE the reason the table renders a merged view, so the
  // merge lives with them: every other consumer (sorting, selection,
  // approval) reads `displayLeads` / `leadsById` from here.
  const displayLeads = leads.map((lead) => ({ ...lead, ...(salesOverrides[lead.borrower_id] ?? {}) }));
  const leadsById = new Map(displayLeads.map((lead) => [lead.borrower_id, lead]));

  function openDisposition(borrowerId: string) {
    const lead = leadsById.get(borrowerId);
    setPendingDisposition(borrowerId);
    setDispositionInitialLo(lead?.assigned_to_email ?? effectiveAssignee);
  }

  function applyAssignmentOverrides(assignments: LeadAssignmentResult[]) {
    setSalesOverrides((current) => {
      const next = { ...current };
      assignments.forEach((assignment) => {
        next[assignment.borrower_id] = {
          ...(next[assignment.borrower_id] ?? {}),
          assigned_to_email: assignment.assigned_to_email,
          assigned_to_label: assignment.assigned_to_label ?? assignment.assigned_to_email,
          assigned_at: assignment.assigned_at,
          assignment_expires_at: assignment.expires_at ?? null,
          // S2 lifecycle chip: a fresh assignment always enters at 'assigned'.
          assignment_status: assignment.status ?? 'assigned',
          // S6: keep the assignment id so the row's lifecycle-advance
          // control can PATCH without a second lookup.
          assignment_id: assignment.assignment_id ?? null,
        };
      });
      return next;
    });
  }

  function applyLeadUpdate(borrowerId: string, update: Partial<LeadSummary>) {
    setSalesOverrides((current) => ({
      ...current,
      [borrowerId]: { ...(current[borrowerId] ?? {}), ...update },
    }));
  }

  /** A sales write is on the wire, from this mount or one that unmounted. */
  function salesWriteInFlight(): boolean {
    return salesInFlightRef.current || queryClient.isMutating({ mutationKey: salesMutationKeys.all }) > 0;
  }

  /** Hold the latch until `write` settles; the latch is released first. */
  function latched(write: Promise<void>): Promise<void> {
    const release = () => {
      salesInFlightRef.current = false;
    };
    void write.then(release, release);
    return write;
  }

  /**
   * After an Undo's 200 (never before): a handed-back row shows its officer
   * again; a released row with no restore reads as unassigned. A no-op once
   * the queue has unmounted (the server state is refetched on return).
   */
  function revertAssignments(released: LeadAssignmentResult[], restored: LeadAssignmentResult[]) {
    const handedBack = new Set(restored.map((row) => row.borrower_id));
    setSalesOverrides((current) => {
      const next = { ...current };
      released.filter((row) => !handedBack.has(row.borrower_id)).forEach((row) => {
        next[row.borrower_id] = {
          ...(next[row.borrower_id] ?? {}),
          assigned_to_email: null,
          assigned_to_label: null,
          assigned_at: null,
          assignment_expires_at: null,
          assignment_status: null,
          assignment_id: null,
        };
      });
      return next;
    });
    applyAssignmentOverrides(restored);
  }

  const undoContext: SalesUndoContext = {
    queryClient,
    requestIdFor: (fingerprint) => requestIds.idFor(fingerprint),
    settle: (fingerprint) => requestIds.settle(fingerprint),
    onReverted: revertAssignments,
  };

  /** One assign / distribute intent; a 429 `retry` re-runs it with the same request id. */
  function runAssign(
    borrowerIds: string[],
    loEmails: string[],
    onAssigned: () => void,
    retry: () => void,
  ): Promise<void> {
    if (salesWriteInFlight()) return Promise.resolve();
    salesInFlightRef.current = true;
    const intent = intentFingerprint('assign', borrowerIds.join(','), loEmails.join(','));
    const variables = { borrowerIds, loEmails, requestId: requestIds.idFor(intent) };
    return latched(assignLeads.run(variables).then(
      (result) => {
        requestIds.settle(intent);
        applyAssignmentOverrides(result.assignments);
        if (isSingleAssignment(variables)) toastAssigned(undoContext, result);
        else toastDistributed(undoContext, result, variables.requestId);
        onAssigned();
      },
      (err: unknown) => {
        toastSalesWriteFailure(ASSIGN_FAILED, err, 'the assignment', retry);
      },
    ));
  }

  /**
   * Assign or round-robin-distribute the caller's selection.
   *
   * `borrowerIds` and `onAssigned` are parameters, not hook inputs, because
   * the selection set lives in `useLeadApprovalActions` — which is
   * constructed from `displayLeads` and therefore after this hook.
   */
  function assignSelected(
    mode: 'selected-lo' | 'round-robin',
    borrowerIds: string[],
    onAssigned: () => void,
  ): Promise<void> {
    if (salesWriteInFlight() || borrowerIds.length === 0) return Promise.resolve();
    const loEmails = mode === 'round-robin'
      ? salesTeam.map((member) => member.email)
      : effectiveAssignee
        ? [effectiveAssignee]
        : [];
    if (loEmails.length === 0) {
      toastWriteRefusal(ASSIGN_FAILED, 'No active loan officers are available for assignment.');
      return Promise.resolve();
    }
    return withRetry((retry) => runAssign(borrowerIds, loEmails, onAssigned, retry));
  }

  /** The open panel's Submit, with the payload the panel validated. */
  function submitDisposition(payload: LeadDispositionPayload): Promise<void> {
    if (!pendingDisposition || salesWriteInFlight()) return Promise.resolve();
    salesInFlightRef.current = true;
    const borrowerId = pendingDisposition;
    const intent = intentFingerprint(
      'disposition', borrowerId, payload.lo_email, payload.outcome, payload.callback_at,
    );
    return latched(logDisposition.mutateAsync({ borrowerId, payload, requestId: requestIds.idFor(intent) }).then(
      (result) => {
        requestIds.settle(intent);
        setSalesOverrides((current) => ({
          ...current,
          [borrowerId]: {
            ...(current[borrowerId] ?? {}),
            assigned_to_email: current[borrowerId]?.assigned_to_email ?? leadsById.get(borrowerId)?.assigned_to_email ?? payload.lo_email,
            latest_disposition_outcome: result.disposition.outcome,
            latest_disposition_at: result.disposition.occurred_at,
            latest_callback_at: result.disposition.callback_at ?? null,
          },
        }));
        toast.success(`${dispositionLabel(result.disposition.outcome)} logged`, {
          detail: `Borrower ${borrowerId}`,
          auditEventId: result.audit_event_id,
        });
        setPendingDisposition(null);
      },
      (err: unknown) => {
        toastWriteFailure("Couldn't log the disposition", err, 'the disposition');
      },
    ));
  }

  return {
    displayLeads,
    leadsById,
    applyLeadUpdate,
    assignSelected,
    selectedAssignee: effectiveAssignee,
    setSelectedAssignee,
    salesBusy,
    pendingDisposition,
    setPendingDisposition,
    dispositionInitialLo,
    openDisposition,
    submitDisposition,
  };
}
