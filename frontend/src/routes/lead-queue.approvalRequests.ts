/**
 * The Lead Queue's approval requests (audit flow-02 / shell-06,
 * D-approval-flow-c): ONE audit-free read of GET /outreach/approval-requests
 * per Lead Queue mount (the keep-alive slot keeps the mount across a dossier
 * and Back), plus the active refetch after a decision, a create or a
 * withdraw (invalidateApprovalRequests). Never polled, never refetched on
 * focus or reconnect, never retried by itself, never read before the session
 * says who is asking: an approver reads every open request (`open`), anyone
 * else their own (`mine`).
 *
 * What the table needs is derived here once per answer, so its identities
 * hold between reads: the per-row marks (OPEN rows only), the chip count and
 * the request a scoped queue shows. Lakebase workflow state, no borrower
 * attribute: no VIEW_* row is written.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { approvalRequestsApi } from '../lib/apiClients/approvalRequests';
import { queryKeys } from '../lib/queryKeys';
import type { ApprovalRequestBatchView, ApprovalRequestList } from '../types/approvalRequests';
import type { ApprovalRequestMark, LeadTableApprovalRequests } from '../components/mortgage/LeadTable.types';

export const APPROVAL_REQUESTS_STALE_MS = 60_000;

interface UseLeadQueueApprovalRequestsInput {
  sessionStatus: 'loading' | 'ready' | 'error';
  canApprove: boolean;
  /** The request a scoped queue shows (`?approval_request_batch=`), else null. */
  scopeBatchId: string | null;
}

const REQUESTED_BY_ME: ApprovalRequestMark = { label: 'Approval requested', tone: 'warning' };

function openRows(batch: ApprovalRequestBatchView) {
  return batch.rows.filter((row) => row.state === 'open');
}

/** The marks and counts one answer implies (pure: pinned in lead-queue.approvalRequests.test). */
export function deriveApprovalRequests(
  data: ApprovalRequestList,
  role: LeadTableApprovalRequests['role'],
  scopeBatchId: string | null,
): Pick<LeadTableApprovalRequests, 'marks' | 'count' | 'batches' | 'scope'> {
  const marks = new Map<string, ApprovalRequestMark>();
  for (const batch of data.batches) {
    const mark = role === 'approver'
      ? { label: `Requested · ${batch.requested_by_display ?? 'a teammate'}`, tone: 'neutral' as const }
      : REQUESTED_BY_ME;
    for (const row of openRows(batch)) marks.set(row.borrower_id, mark);
  }
  const scopeBatch = scopeBatchId === null
    ? null
    : data.batches.find((batch) => batch.batch_id === scopeBatchId && openRows(batch).length > 0) ?? null;
  return {
    marks,
    count: role === 'approver' ? marks.size : data.batches.length,
    batches: data.batches,
    scope: scopeBatchId === null ? null : { batchId: scopeBatchId, batch: scopeBatch },
  };
}

const NO_MARKS: ReadonlyMap<string, ApprovalRequestMark> = new Map();

export function useLeadQueueApprovalRequests({
  sessionStatus,
  canApprove,
  scopeBatchId,
}: UseLeadQueueApprovalRequestsInput): LeadTableApprovalRequests | null {
  const role: LeadTableApprovalRequests['role'] = canApprove ? 'approver' : 'requester';
  const scope = canApprove ? 'open' : 'mine';
  const ready = sessionStatus === 'ready';
  const query = useQuery({
    queryKey: queryKeys.approvalRequests(scope),
    queryFn: ({ signal }) => approvalRequestsApi.list(scope, signal),
    enabled: ready,
    staleTime: APPROVAL_REQUESTS_STALE_MS,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  const { data, isError, refetch } = query;
  const derived = useMemo(
    () => (data ? deriveApprovalRequests(data, role, scopeBatchId) : null),
    [data, role, scopeBatchId],
  );
  return useMemo(() => {
    if (!ready) return null;
    const retry = () => {
      void refetch();
    };
    if (derived) return { role, status: 'ready', retry, ...derived };
    return {
      role,
      status: isError ? 'error' : 'pending',
      marks: NO_MARKS,
      count: 0,
      batches: [],
      scope: scopeBatchId === null ? null : { batchId: scopeBatchId, batch: null },
      retry,
    };
  }, [ready, derived, role, isError, refetch, scopeBatchId]);
}
