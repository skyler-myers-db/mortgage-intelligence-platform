/**
 * useLeadSalesActions — the sales-operations half of the ranked-borrower
 * table: optimistic per-row overrides from assignment / lifecycle writes (and
 * therefore the merged `displayLeads` view every other consumer reads), assign
 * + round-robin distribution, and the call-disposition panel's form state.
 * Extracted from LeadTable.tsx (file-size gate, plan item 2).
 *
 * The writes run on the sales mutations (lib/mutations/sales, audit stack-09
 * / wow-power-5 step 1): networkMode 'always', one request_id per intent,
 * and an honest strategy ('manual' for one loan officer, 'round_robin' for
 * two or more; 'score_balanced' is never sent). Results and write failures
 * go to the shell toast region with the audit event id; pre-flight
 * validation stays in the table's alert.
 */

import { useRef, useState } from 'react';
import { useIsMutating, type QueryClient } from '@tanstack/react-query';
import type { CallDisposition, LeadSummary, SalesTeamMember } from '../../types';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import { salesMutationKeys, useAssignLeads, useLogDisposition } from '../../lib/mutations/sales';
import { toast } from '../../lib/toast';
import { dispositionLabel } from './LeadTable.logic';

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

export interface UseLeadSalesActionsInput {
  leads: LeadSummary[];
  salesTeam: SalesTeamMember[];
  queryClient: QueryClient;
  /** Shared with the approval hook: the table renders one error alert. */
  setApprovalError: (message: string | null) => void;
}

export function useLeadSalesActions({
  leads,
  salesTeam,
  queryClient,
  setApprovalError,
}: UseLeadSalesActionsInput) {
  const [selectedAssignee, setSelectedAssignee] = useState<string>('');
  const [salesOverrides, setSalesOverrides] = useState<Record<string, Partial<LeadSummary>>>({});
  const [pendingDisposition, setPendingDisposition] = useState<string | null>(null);
  const [dispositionOutcome, setDispositionOutcome] = useState<CallDisposition['outcome']>('called_left_voicemail');
  const [dispositionLo, setDispositionLo] = useState<string>('');
  const [dispositionCallbackAt, setDispositionCallbackAt] = useState('');
  const [dispositionNotes, setDispositionNotes] = useState('');
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
    setDispositionLo(lead?.assigned_to_email ?? effectiveAssignee);
    setDispositionOutcome('called_left_voicemail');
    setDispositionCallbackAt('');
    setDispositionNotes('');
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
      setApprovalError('No active loan officers are available for assignment.');
      return Promise.resolve();
    }
    salesInFlightRef.current = true;
    setApprovalError(null);
    const intent = intentFingerprint('assign', borrowerIds.join(','), loEmails.join(','));
    return latched(assignLeads.run({ borrowerIds, loEmails, requestId: requestIds.idFor(intent) }).then(
      (result) => {
        requestIds.settle(intent);
        applyAssignmentOverrides(result.assignments);
        const noun = result.assigned_count === 1 ? 'lead' : 'leads';
        toast.success(`${result.assigned_count} ${noun} assigned`, { auditEventId: result.audit_event_id });
        onAssigned();
      },
      (err: unknown) => {
        toast.error("Couldn't assign the selected leads", { detail: err instanceof Error ? err.message : null });
      },
    ));
  }

  function submitDisposition(): Promise<void> {
    if (!pendingDisposition || salesWriteInFlight()) return Promise.resolve();
    if (!dispositionLo) {
      setApprovalError('Choose the loan officer who worked this lead.');
      return Promise.resolve();
    }
    if (dispositionOutcome === 'callback_scheduled' && !dispositionCallbackAt) {
      setApprovalError('Callback scheduled dispositions require a callback time.');
      return Promise.resolve();
    }
    salesInFlightRef.current = true;
    setApprovalError(null);
    const borrowerId = pendingDisposition;
    const payload = {
      lo_email: dispositionLo,
      outcome: dispositionOutcome,
      callback_at: dispositionCallbackAt ? new Date(dispositionCallbackAt).toISOString() : null,
      notes: dispositionNotes.trim() || null,
    };
    const intent = intentFingerprint(
      'disposition', borrowerId, payload.lo_email, payload.outcome, payload.callback_at, payload.notes,
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
        toast.error("Couldn't log the disposition", { detail: err instanceof Error ? err.message : null });
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
    openDisposition,
    submitDisposition,
    dispositionOutcome,
    setDispositionOutcome,
    dispositionLo,
    setDispositionLo,
    dispositionCallbackAt,
    setDispositionCallbackAt,
    dispositionNotes,
    setDispositionNotes,
  };
}
