/**
 * My requests, the requester's panel (audit flow-02 / shell-06,
 * D-approval-flow-c record step 18; deviation:request-approval). Lazy: opened
 * from the "My requests · n" chip. Per request: its age, the "Reason for the
 * request" (O3), each borrower's outcome (derived server-side, never stored
 * as approved or rejected) and, while any borrower is still open, Withdraw
 * behind an inline confirm. Withdraw is one POST (APPROVAL_REQUEST_WITHDRAWN
 * when it closed anything); the toast and the unmarked rows follow the 200.
 *
 * Focus never falls to <body> when the focused control unmounts: opening
 * the confirm moves it to the confirm's Withdraw, Keep returns it to the
 * Withdraw that opened it, and a successful withdraw (whose actions unmount
 * once the refetched rows are no longer open) moves it to the panel title.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { formatCount } from '../../lib/formatters';
import { useWithdrawApprovalRequest } from '../../lib/mutations/approvalRequests';
import { formatRelative } from '../../lib/time';
import { toast } from '../../lib/toast';
import type { ApprovalRequestBatchView, ApprovalRequestRow } from '../../types/approvalRequests';
import { Button, SurfaceTitle } from '../Primitives';
import { REQUEST_REASON_LABEL } from './approvalRequestCopy';
import type { LeadTableApprovalRequests } from './LeadTable.types';
import { describeWriteFailure } from './leadWriteFailureToast';
import './ApprovalRequests.css';

/** Each derived state in words (exhaustive: a new state fails to compile until it has one). */
export const REQUEST_ROW_STATE_LABELS: Readonly<Record<ApprovalRequestRow['state'], string>> = {
  open: 'Waiting for an approver',
  approved: 'Approved',
  rejected: 'Rejected',
  decided_outside: 'Decided without this request',
  withdrawn: 'Withdrawn',
  expired: 'Expired',
};

export interface MyApprovalRequestsPanelProps {
  requests: LeadTableApprovalRequests;
  onClose: () => void;
}

interface MyRequestProps {
  batch: ApprovalRequestBatchView;
  /** After the 200: focus goes to the panel title (this request's actions are about to unmount). */
  onWithdrawn: () => void;
}

function MyRequest({ batch, onWithdrawn }: MyRequestProps) {
  const queryClient = useQueryClient();
  const withdraw = useWithdrawApprovalRequest(queryClient);
  const [confirming, setConfirming] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  // Set by the click that opens or closes the confirm, read once its render committed.
  const focusNext = useRef<'confirm' | 'trigger' | null>(null);
  useEffect(() => {
    const target = focusNext.current;
    focusNext.current = null;
    if (target === 'confirm') confirmRef.current?.focus();
    else if (target === 'trigger') triggerRef.current?.focus();
  }, [confirming]);
  const toggleConfirm = (next: boolean) => {
    focusNext.current = next ? 'confirm' : 'trigger';
    setConfirming(next);
  };
  // A synchronous latch: TanStack Query publishes isPending on a later task,
  // so a second click inside that window would otherwise send a second POST.
  const inFlight = useRef(false);
  const open = batch.rows.filter((row) => row.state === 'open').length;
  const confirmWithdraw = () => {
    if (inFlight.current || withdraw.isPending) return;
    inFlight.current = true;
    withdraw.mutate(batch.batch_id, {
      onSettled: () => {
        inFlight.current = false;
      },
      onSuccess: (result) => {
        // Before the confirm unmounts, so focus never passes through <body>.
        onWithdrawn();
        setConfirming(false);
        if (result.withdrawn_now > 0) toast.success('Request withdrawn', { auditEventId: result.audit_event_id });
        else toast.info('Nothing was open to withdraw');
      },
      onError: (error) => {
        void describeWriteFailure(error, 'the withdrawal').then((detail) => {
          toast.error("Couldn't withdraw the request", { detail });
        });
      },
    });
  };
  return (
    <section className="approval-requests__item" aria-label={`Request from ${formatRelative(batch.created_at)}`}>
      <div className="approval-requests__meta">
        <span>{formatRelative(batch.created_at)}</span>
        <span>{formatCount(batch.rows.length)} {batch.rows.length === 1 ? 'borrower' : 'borrowers'}</span>
      </div>
      <p className="approval-requests__reason">
        <span className="field__label">{REQUEST_REASON_LABEL}</span> {batch.rationale}
      </p>
      <ul className="approval-requests__rows">
        {batch.rows.map((row) => (
          <li key={row.borrower_id} className="approval-requests__row">
            <span className="mono">{row.borrower_id}</span>
            <span>{REQUEST_ROW_STATE_LABELS[row.state]}</span>
          </li>
        ))}
      </ul>
      {open > 0 && (
        <div className="approval-requests__actions">
          {confirming ? (
            <>
              <span className="fs-12">
                Withdraw {formatCount(open)} open {open === 1 ? 'borrower' : 'borrowers'}? Approvers stop seeing them.
              </span>
              <Button
                ref={confirmRef}
                size="sm"
                variant="danger"
                loading={withdraw.isPending}
                onClick={confirmWithdraw}
                data-testid={`approval-request-withdraw-confirm-${batch.batch_id}`}
              >
                Withdraw
              </Button>
              <Button size="sm" variant="ghost" onClick={() => toggleConfirm(false)} data-testid={`approval-request-withdraw-keep-${batch.batch_id}`}>
                Keep
              </Button>
            </>
          ) : (
            <Button ref={triggerRef} size="sm" onClick={() => toggleConfirm(true)} data-testid={`approval-request-withdraw-${batch.batch_id}`}>
              Withdraw
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

export function MyApprovalRequestsPanel({ requests, onClose }: MyApprovalRequestsPanelProps) {
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => pushEscapeLayer(() => {
    closeRef.current();
    return true;
  }), []);
  return (
    <div
      className="filter-menu approval-requests__panel"
      role="dialog"
      aria-labelledby={titleId}
      data-testid="my-approval-requests-panel"
    >
      {/* tabIndex -1: the focus target after a withdraw (DecisionReceipt's heading precedent). */}
      <SurfaceTitle level={3} id={titleId} ref={titleRef} tabIndex={-1} className="approval-requests__panel-title">
        My requests
      </SurfaceTitle>
      {requests.batches.length === 0 && <p className="approval-requests__state">You have no approval requests.</p>}
      {requests.batches.map((batch) => (
        <MyRequest key={batch.batch_id} batch={batch} onWithdrawn={() => titleRef.current?.focus()} />
      ))}
    </div>
  );
}
