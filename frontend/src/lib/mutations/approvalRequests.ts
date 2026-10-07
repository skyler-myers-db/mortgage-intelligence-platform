/**
 * Approval-request writes on the TanStack mutation layer (audit flow-02 /
 * shell-06, D-approval-flow-c). Same invariants as the decision mutations
 * (lib/mutations/outreach.ts):
 *
 *   - PESSIMISTIC: no onMutate, no setQueryData. A request's chips appear
 *     only after the 200, through the active refetch of the audit-free list.
 *   - networkMode 'always', retry false: a write started offline fails at
 *     once instead of firing on reconnect unconfirmed.
 *   - One request_key per intent (the sorted borrower ids and the reason),
 *     settled on the 200 and reused when the same content is sent again, so
 *     the server replays a retry without a second audit row.
 *   - onSuccess refetches only the ACTIVE approval-request list
 *     (invalidateApprovalRequests): never invalidateOperationalQueries, and
 *     never an audited read. Nothing here drafts (/outreach/draft is never
 *     called on the request path).
 *
 * Imported by the lazy request form and My requests panel only.
 */
import { useMutation, type QueryClient } from '@tanstack/react-query';
import type { ApprovalRequestCreated, ApprovalRequestWithdrawn } from '../../types/approvalRequests';
import { approvalRequestsApi } from '../apiClients/approvalRequests';
import { invalidateApprovalRequests } from './outreach';
import { intentFingerprint, useIntentRequestIds } from './requestIds';

export const approvalRequestMutationKeys = {
  create: ['mip', 'approval-requests', 'create'] as const,
  withdraw: ['mip', 'approval-requests', 'withdraw'] as const,
};

export interface CreateApprovalRequestVariables {
  borrowerIds: readonly string[];
  rationale: string;
}

/** The request's intent: the borrowers in a stable order, and the reason as sent. */
export function approvalRequestIntent({ borrowerIds, rationale }: CreateApprovalRequestVariables): string {
  return intentFingerprint('approval-request', [...borrowerIds].sort().join(','), rationale);
}

export function useCreateApprovalRequest(queryClient: QueryClient) {
  const requestKeys = useIntentRequestIds();
  const mutation = useMutation<ApprovalRequestCreated, Error, CreateApprovalRequestVariables>(
    {
      mutationKey: approvalRequestMutationKeys.create,
      mutationFn: (variables) => {
        const intent = approvalRequestIntent(variables);
        return approvalRequestsApi
          .create({
            borrower_ids: [...variables.borrowerIds],
            rationale: variables.rationale,
            request_key: requestKeys.idFor(intent),
          })
          .then((created) => {
            requestKeys.settle(intent);
            return created;
          });
      },
      networkMode: 'always',
      retry: false,
      onSuccess: () => {
        void invalidateApprovalRequests(queryClient);
      },
    },
    queryClient,
  );
  return mutation;
}

export function useWithdrawApprovalRequest(queryClient: QueryClient) {
  return useMutation<ApprovalRequestWithdrawn, Error, string>(
    {
      mutationKey: approvalRequestMutationKeys.withdraw,
      mutationFn: (batchId) => approvalRequestsApi.withdraw(batchId),
      networkMode: 'always',
      retry: false,
      onSuccess: () => {
        void invalidateApprovalRequests(queryClient);
      },
    },
    queryClient,
  );
}
