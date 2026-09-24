/**
 * Sales-operations writes on the TanStack mutation layer (audit stack-09,
 * wow-power-5 step 1): assign, distribute and log a disposition.
 *
 * The distribution strategy is honest. The store allocates round-robin in
 * request order for every strategy (backend/services/sales_state_writes.py),
 * so the queue sends 'manual' for one loan officer and 'round_robin' for two
 * or more. 'score_balanced' is never sent: it used to be, and the audit row
 * recorded a balancing that never ran.
 *
 * Same write posture as outreach.ts: networkMode 'always' (a write started
 * offline fails at once instead of firing on reconnect), retry false, one
 * request_id per intent in the variables, invalidation only through
 * invalidateOperationalQueries (refetchType 'none'), no shared scope.
 */
import { useMutation, type QueryClient } from '@tanstack/react-query';
import type { CallDisposition, LeadAssignment } from '../../types';
import { api } from '../api';
import type { DispositionResponse } from '../apiTypes';
import type { SalesSendStrategy } from '../apiClients/sales';
import { invalidateOperationalQueries } from '../queryKeys';

export const salesMutationKeys = {
  all: ['mip', 'sales'] as const,
  assign: ['mip', 'sales', 'assign'] as const,
  distribute: ['mip', 'sales', 'distribute'] as const,
  disposition: ['mip', 'sales', 'disposition'] as const,
};

/** The strategy a distribution to these loan officers really runs. */
export function distributionStrategy(loEmails: readonly string[]): SalesSendStrategy {
  return loEmails.length === 1 ? 'manual' : 'round_robin';
}

export interface AssignLeadsVariables {
  borrowerIds: string[];
  loEmails: string[];
  requestId: string;
}

export interface AssignLeadsResult {
  assigned_count: number;
  assignments: LeadAssignment[];
  audit_event_id: string | null;
}

export interface LogDispositionVariables {
  borrowerId: string;
  requestId: string;
  payload: {
    lo_email: string;
    outcome: CallDisposition['outcome'];
    callback_at: string | null;
    notes: string | null;
  };
}

/** One borrower to one loan officer is an assignment; anything else a distribution. */
export function isSingleAssignment(variables: Pick<AssignLeadsVariables, 'borrowerIds' | 'loEmails'>): boolean {
  return variables.borrowerIds.length === 1 && variables.loEmails.length === 1;
}

export function useAssignLeads(queryClient: QueryClient) {
  const onSuccess = () => {
    void invalidateOperationalQueries(queryClient);
  };
  const assign = useMutation<AssignLeadsResult, Error, AssignLeadsVariables>(
    {
      mutationKey: salesMutationKeys.assign,
      mutationFn: async ({ borrowerIds, loEmails, requestId }) => {
        const result = await api.assignLead(borrowerIds[0], loEmails[0], 'manual', undefined, requestId);
        return {
          assigned_count: 1,
          assignments: [result.assignment],
          audit_event_id: result.audit_event_id ?? null,
        };
      },
      networkMode: 'always',
      retry: false,
      onSuccess,
    },
    queryClient,
  );
  const distribute = useMutation<AssignLeadsResult, Error, AssignLeadsVariables>(
    {
      mutationKey: salesMutationKeys.distribute,
      mutationFn: async ({ borrowerIds, loEmails, requestId }) => {
        const result = await api.distributeLeads(
          borrowerIds,
          loEmails,
          distributionStrategy(loEmails),
          undefined,
          requestId,
        );
        return {
          assigned_count: result.assigned_count,
          assignments: result.assignments,
          audit_event_id: result.audit_event_id ?? null,
        };
      },
      networkMode: 'always',
      retry: false,
      onSuccess,
    },
    queryClient,
  );
  return {
    /** Assign or distribute, by the shape of the intent. */
    run(variables: AssignLeadsVariables): Promise<AssignLeadsResult> {
      return isSingleAssignment(variables)
        ? assign.mutateAsync(variables)
        : distribute.mutateAsync(variables);
    },
  };
}

export function useLogDisposition(queryClient: QueryClient) {
  return useMutation<DispositionResponse, Error, LogDispositionVariables>(
    {
      mutationKey: salesMutationKeys.disposition,
      mutationFn: ({ borrowerId, payload, requestId }) =>
        api.logDisposition(borrowerId, payload, undefined, requestId),
      networkMode: 'always',
      retry: false,
      onSuccess: () => {
        void invalidateOperationalQueries(queryClient);
      },
    },
    queryClient,
  );
}
