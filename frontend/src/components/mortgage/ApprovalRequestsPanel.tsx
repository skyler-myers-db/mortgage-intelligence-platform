/**
 * Approval requests, the approver's panel (audit flow-02 / shell-06,
 * D-approval-flow-c record step 19; deviation:request-approval). Lazy: opened
 * from the "Approval requests · N" chip. Every open request, oldest first (the
 * server's order): who asked (a display label, never an email), how many
 * borrowers are still open, the age and the "Reason for the request" (O3).
 * "Open in queue" opens the request as a scoped Lead Queue (a new history
 * entry, every other filter and any campaign binding cleared), where each
 * decision carries the request's id. Reads nothing itself: the rows come
 * from the queue's one audit-free list read.
 */
import { useEffect, useId, useRef } from 'react';
import { useNavigate } from 'react-router';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { formatCount } from '../../lib/formatters';
import { formatRelative } from '../../lib/time';
import type { ApprovalRequestBatchView } from '../../types/approvalRequests';
import { Button, SurfaceTitle } from '../Primitives';
import { REQUEST_REASON_LABEL } from './approvalRequestCopy';
import type { LeadTableApprovalRequests } from './LeadTable.types';
import './ApprovalRequests.css';

export interface ApprovalRequestsPanelProps {
  requests: LeadTableApprovalRequests;
  /** Escape or a pick: the chip closes the panel and takes focus back. */
  onClose: () => void;
}

/** The scoped queue for one request: only the scope param (a new entry). */
export function approvalRequestQueueHref(batchId: string): string {
  return `/lead-queue?${new URLSearchParams({ approval_request_batch: batchId }).toString()}`;
}

function openCount(batch: ApprovalRequestBatchView): number {
  return batch.rows.filter((row) => row.state === 'open').length;
}

export function ApprovalRequestsPanel({ requests, onClose }: ApprovalRequestsPanelProps) {
  const navigate = useNavigate();
  const titleId = useId();
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => pushEscapeLayer(() => {
    closeRef.current();
    return true;
  }), []);
  const open = requests.batches.filter((batch) => openCount(batch) > 0);
  return (
    <div
      className="filter-menu approval-requests__panel"
      role="dialog"
      aria-labelledby={titleId}
      data-testid="approval-requests-panel"
    >
      <SurfaceTitle level={3} id={titleId} className="approval-requests__panel-title">Approval requests</SurfaceTitle>
      {open.length === 0 && <p className="approval-requests__state">No open approval requests.</p>}
      {open.map((batch) => (
        <section key={batch.batch_id} className="approval-requests__item" aria-label={`Request from ${batch.requested_by_display ?? 'a teammate'}`}>
          <div className="approval-requests__meta">
            <span className="text-1">{batch.requested_by_display ?? 'A teammate'}</span>
            <span className="chip chip--neutral"><span className="chip__label">{formatCount(openCount(batch))} open</span></span>
            <span>{formatRelative(batch.created_at)}</span>
          </div>
          <p className="approval-requests__reason">
            <span className="field__label">{REQUEST_REASON_LABEL}</span> {batch.rationale}
          </p>
          <div className="approval-requests__actions">
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                onClose();
                void navigate(approvalRequestQueueHref(batch.batch_id));
              }}
              data-testid={`approval-request-open-${batch.batch_id}`}
            >
              Open in queue
            </Button>
          </div>
        </section>
      ))}
    </div>
  );
}
