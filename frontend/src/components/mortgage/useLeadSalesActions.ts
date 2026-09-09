/**
 * useLeadSalesActions — the sales-operations half of the ranked-borrower
 * table: optimistic per-row overrides from assignment / lifecycle writes (and
 * therefore the merged `displayLeads` view every other consumer reads), assign
 * + round-robin distribution, the call-disposition panel's form state, and the
 * sales toast. Extracted from LeadTable.tsx (file-size gate, plan item 2);
 * state ownership and effect order are unchanged.
 */

import { useEffect, useState } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { CallDisposition, LeadSummary, SalesTeamMember } from '../../types';
import { api } from '../../lib/api';
import { invalidateOperationalQueries } from '../../lib/queryKeys';
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
  'use no memo';

  const [selectedAssignee, setSelectedAssignee] = useState<string>('');
  const [salesToast, setSalesToast] = useState<string | null>(null);
  const [salesBusy, setSalesBusy] = useState(false);
  const [salesOverrides, setSalesOverrides] = useState<Record<string, Partial<LeadSummary>>>({});
  const [pendingDisposition, setPendingDisposition] = useState<string | null>(null);
  const [dispositionOutcome, setDispositionOutcome] = useState<CallDisposition['outcome']>('called_left_voicemail');
  const [dispositionLo, setDispositionLo] = useState<string>('');
  const [dispositionCallbackAt, setDispositionCallbackAt] = useState('');
  const [dispositionNotes, setDispositionNotes] = useState('');

  // The overrides ARE the reason the table renders a merged view, so the
  // merge lives with them: every other consumer (sorting, selection,
  // approval) reads `displayLeads` / `leadsById` from here.
  const displayLeads = leads.map((lead) => ({ ...lead, ...(salesOverrides[lead.borrower_id] ?? {}) }));
  const leadsById = new Map(displayLeads.map((lead) => [lead.borrower_id, lead]));

  useEffect(() => {
    if (!selectedAssignee && salesTeam.length > 0) {
      setSelectedAssignee(salesTeam[0].email);
    }
  }, [salesTeam, selectedAssignee]);

  function openDisposition(borrowerId: string) {
    const lead = leadsById.get(borrowerId);
    setPendingDisposition(borrowerId);
    setDispositionLo(lead?.assigned_to_email ?? selectedAssignee ?? salesTeam[0]?.email ?? '');
    setDispositionOutcome('called_left_voicemail');
    setDispositionCallbackAt('');
    setDispositionNotes('');
  }

  useEffect(() => {
    if (!salesToast) return;
    const t = window.setTimeout(() => setSalesToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [salesToast]);

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

  /**
   * Assign or round-robin-distribute the caller's selection.
   *
   * `borrowerIds` and `onAssigned` are parameters, not hook inputs, because
   * the selection set lives in `useLeadApprovalActions` — which is
   * constructed from `displayLeads` and therefore after this hook. Passing
   * them in keeps the success path's setState order identical to the
   * pre-split component (toast, then clear selection, then busy=false).
   */
  async function assignSelected(
    mode: 'selected-lo' | 'round-robin',
    borrowerIds: string[],
    onAssigned: () => void,
  ) {
    if (salesBusy) return;
    if (borrowerIds.length === 0) return;
    const loEmails = mode === 'round-robin'
      ? salesTeam.map((member) => member.email)
      : selectedAssignee
        ? [selectedAssignee]
        : [];
    if (loEmails.length === 0) {
      setApprovalError('No active loan officers are available for assignment.');
      return;
    }
    setSalesBusy(true);
    setApprovalError(null);
    try {
      const result = borrowerIds.length === 1 && loEmails.length === 1
        ? {
            assigned_count: 1,
            assignments: [(await api.assignLead(borrowerIds[0], loEmails[0])).assignment],
          }
        : await api.distributeLeads(borrowerIds, loEmails, mode === 'round-robin' ? 'round_robin' : 'score_balanced');
      applyAssignmentOverrides(result.assignments);
      void invalidateOperationalQueries(queryClient);
      setSalesToast(`${result.assigned_count} ${result.assigned_count === 1 ? 'lead' : 'leads'} assigned`);
      onAssigned();
    } catch (err: unknown) {
      setApprovalError(
        err instanceof Error
          ? `Couldn't assign selected leads: ${err.message}`
          : "Couldn't assign selected leads.",
      );
    } finally {
      setSalesBusy(false);
    }
  }

  async function submitDisposition() {
    if (!pendingDisposition) return;
    if (!dispositionLo) {
      setApprovalError('Choose the loan officer who worked this lead.');
      return;
    }
    if (dispositionOutcome === 'callback_scheduled' && !dispositionCallbackAt) {
      setApprovalError('Callback scheduled dispositions require a callback time.');
      return;
    }
    setSalesBusy(true);
    setApprovalError(null);
    try {
      const result = await api.logDisposition(pendingDisposition, {
        lo_email: dispositionLo,
        outcome: dispositionOutcome,
        callback_at: dispositionCallbackAt ? new Date(dispositionCallbackAt).toISOString() : null,
        notes: dispositionNotes.trim() || null,
      });
      setSalesOverrides((current) => ({
        ...current,
        [pendingDisposition]: {
          ...(current[pendingDisposition] ?? {}),
          assigned_to_email: current[pendingDisposition]?.assigned_to_email ?? leadsById.get(pendingDisposition)?.assigned_to_email ?? dispositionLo,
          latest_disposition_outcome: result.disposition.outcome,
          latest_disposition_at: result.disposition.occurred_at,
          latest_callback_at: result.disposition.callback_at ?? null,
        },
      }));
      setSalesToast(`${dispositionLabel(result.disposition.outcome)} logged for ${pendingDisposition}`);
      void invalidateOperationalQueries(queryClient);
      setPendingDisposition(null);
    } catch (err: unknown) {
      setApprovalError(
        err instanceof Error
          ? `Couldn't log disposition: ${err.message}`
          : "Couldn't log disposition.",
      );
    } finally {
      setSalesBusy(false);
    }
  }

  return {
    displayLeads,
    leadsById,
    applyLeadUpdate,
    assignSelected,
    selectedAssignee,
    setSelectedAssignee,
    salesToast,
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
