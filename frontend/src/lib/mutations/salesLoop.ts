/**
 * The sales-loop writes on the TanStack mutation layer (W5d w5-sales-loop):
 * the Undo releases behind the assign / distribute toasts (audit states-07
 * item 1) and the Distribution Board's dry run and commit (wow-power-5 step 2).
 *
 * The releases run through a bare `MutationObserver`, not a hook: an Undo is
 * pressed on a shell toast that outlives the Lead Queue, so a hook's observer
 * may be gone. The mutation still lands in the MutationCache under
 * ['mip','sales', …], which keeps the queue's own-write store and `salesBusy`
 * honest whether or not the queue is mounted.
 *
 * The Board's dry run is keyed ['mip','distribution-preview'], OUTSIDE
 * ['mip','sales']: it writes nothing, so it is not an own write and must not
 * set `salesBusy`. The commit uses the distribute key like any other run.
 *
 * Same write posture as lib/mutations/sales: networkMode 'always', retry
 * false (a retry re-runs the same intent with the same request id, never a
 * TanStack resend), invalidation only through invalidateOperationalQueries.
 * Loaded lazily (the Undo action and the Board import it on demand).
 */
import { MutationObserver, useMutation, type MutationKey, type QueryClient } from '@tanstack/react-query';
import {
  salesLoopApi,
  type AssignmentReleaseResponse,
  type DistributionPlanInput,
  type DistributionReleaseResponse,
} from '../apiClients/salesLoop';
import type { DistributeLeadsResponse } from '../apiClients/sales';
import { invalidateOperationalQueries } from '../queryKeys';
import { salesMutationKeys } from './sales';

/** The dry run's key: deliberately not under ['mip','sales'] (no own write). */
export const distributionPreviewKey = ['mip', 'distribution-preview'] as const;

export interface ReleaseAssignmentVariables {
  borrowerId: string;
  assignmentId: string;
  requestId: string;
}

export interface ReleaseDistributionVariables {
  distributionRequestId: string;
  requestId: string;
}

export interface CommitDistributionVariables extends DistributionPlanInput {
  fingerprint: string;
  requestId: string;
}

function observedWrite<TData, TVariables>(
  queryClient: QueryClient,
  mutationKey: MutationKey,
  mutationFn: (variables: TVariables) => Promise<TData>,
  variables: TVariables,
): Promise<TData> {
  const observer = new MutationObserver<TData, Error, TVariables>(queryClient, {
    mutationKey,
    mutationFn,
    networkMode: 'always',
    retry: false,
    onSuccess: () => {
      void invalidateOperationalQueries(queryClient);
    },
  });
  return observer.mutate(variables);
}

/** Undo one assignment: POST /leads/{id}/assignment/release. */
export function releaseAssignmentWrite(
  queryClient: QueryClient,
  variables: ReleaseAssignmentVariables,
): Promise<AssignmentReleaseResponse> {
  return observedWrite(
    queryClient,
    salesMutationKeys.release,
    ({ borrowerId, assignmentId, requestId }: ReleaseAssignmentVariables) =>
      salesLoopApi.releaseAssignment(borrowerId, { assignment_id: assignmentId, request_id: requestId }),
    variables,
  );
}

/** Undo one distribution run: POST /sales/distribute/release. */
export function releaseDistributionWrite(
  queryClient: QueryClient,
  variables: ReleaseDistributionVariables,
): Promise<DistributionReleaseResponse> {
  return observedWrite(
    queryClient,
    salesMutationKeys.releaseDistribution,
    ({ distributionRequestId, requestId }: ReleaseDistributionVariables) =>
      salesLoopApi.releaseDistribution({ distribution_request_id: distributionRequestId, request_id: requestId }),
    variables,
  );
}

/** The Board's dry run (audit-free, writes nothing). */
export function useDistributionPreview(queryClient: QueryClient) {
  return useMutation<DistributeLeadsResponse, Error, DistributionPlanInput>(
    {
      mutationKey: distributionPreviewKey,
      mutationFn: (input) => salesLoopApi.previewDistribution(input),
      networkMode: 'always',
      retry: false,
    },
    queryClient,
  );
}

/** The Board's commit, fingerprint-checked by the server. */
export function useCommitDistribution(queryClient: QueryClient) {
  return useMutation<DistributeLeadsResponse, Error, CommitDistributionVariables>(
    {
      mutationKey: salesMutationKeys.distribute,
      mutationFn: (variables) => salesLoopApi.commitDistribution(variables),
      networkMode: 'always',
      retry: false,
      onSuccess: () => {
        void invalidateOperationalQueries(queryClient);
      },
    },
    queryClient,
  );
}
