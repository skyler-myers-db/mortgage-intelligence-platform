/**
 * Revoke an approval (audit flow-v2; deviation:revoke-approval). Lazy: the
 * Lead Queue's Approved-row Revoke and its in-session receipt load it on an
 * explicit open, and nothing here runs before that.
 *
 * Pessimistic: the borrower reads Approved until the POST returned
 * revoked = true; only then does the session's approval override go, the
 * toast show and focus return to the row. Nothing is sent to the borrower;
 * the server releases an unworked assignment, and the approval stays in the
 * audit ledger (an OUTREACH_REVOKE row beside it).
 *
 * Without an approval id (the Approved-row path) it reads the borrower's
 * lifecycle ONCE on open (audit-free) to name the approval, and shows why
 * nothing can be revoked instead of the form when there is no current
 * approval or outreach has started. Every failure stays inline with the
 * typed reason kept, in closed copy (RevokeApprovalPanel.failure, loaded on
 * the failure only) that claims a record only when one was written.
 */
import { useContext, useEffect, useId, useRef, useState, type ContextType } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { pushEscapeLayer } from '../../lib/escapeStack';
import type { useRevokeApproval } from '../../lib/mutations/outreach';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import { queryKeys } from '../../lib/queryKeys';
import { toast } from '../../lib/toast';
import { AppStoreContext } from '../appStore';
import { Button, SurfaceTitle } from '../Primitives';
import type { RevokeFailureCopy, RevokeFailureDeps } from './RevokeApprovalPanel.failure';
import './RevokeApprovalPanel.css';

export const REVOKE_COPY = {
  consequence: 'The borrower returns to pending. Nothing is sent; an unworked assignment is released. '
    + 'The approval stays in the audit ledger.',
  hint: 'Leave out personal details.',
  noApproval: 'This borrower has no current approval to revoke.',
  outreachStarted: 'Outreach has started for this borrower, so the approval can no longer be revoked.',
  checking: 'Checking the current approval…',
  checkFailed: "Couldn't check this borrower's approval. Close and try again.",
} as const;

/** The failure copy could not load (a stale deploy, offline): a fixed line, never the raw error. */
const FAILED_FALLBACK: RevokeFailureCopy = { text: "Couldn't revoke the approval.", final: false };

/**
 * The failure copy's chunk, loaded on a failure only. Module level: an
 * import() inside the component is a React Compiler bailout (BuildHIR
 * "Handle Import expressions") that would leave the whole panel uncompiled.
 */
function loadFailureCopy() {
  return import('./RevokeApprovalPanel.failure');
}

export interface RevokeApprovalPanelProps {
  borrowerId: string;
  /** The approval to revoke, when the opener knows it (the receipt); null reads the lifecycle on open. */
  approvalId: string | null;
  /** Cancel, Escape or a recorded revoke: the parent closes the panel. */
  onClose: () => void;
  /** The shell's revoke mutation (it outlives the panel: a late 429 Retry still lands). */
  revoke: ReturnType<typeof useRevokeApproval>;
  /** The queue's write-failure toast and sentence, handed in (no shared-chunk import here). */
  failureDeps: RevokeFailureDeps;
}

type AppStore = NonNullable<ContextType<typeof AppStoreContext>>;

function dropOverride(store: AppStore | null, borrowerId: string): void {
  store?.update('approvals', (current) => {
    if (!(borrowerId in current)) return current;
    const next = { ...current };
    delete next[borrowerId];
    return next;
  });
}

export default function RevokeApprovalPanel({
  borrowerId,
  approvalId,
  onClose,
  revoke,
  failureDeps,
}: RevokeApprovalPanelProps) {
  const requestIds = useIntentRequestIds();
  const store = useContext(AppStoreContext);
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<RevokeFailureCopy | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);
  const inFlight = useRef(false);
  const liveRef = useRef(true);
  const [opener] = useState(() => (typeof document === 'undefined' ? null : document.activeElement));
  const titleId = useId();
  const hintId = useId();
  // The Approved-row path names no approval: one audit-free lifecycle read, on this open only.
  const lifecycle = useQuery({
    queryKey: queryKeys.borrowerLifecycle(borrowerId),
    queryFn: ({ signal }) => api.borrowerLifecycle(borrowerId, signal),
    enabled: approvalId === null,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const life = approvalId === null ? lifecycle.data : null;
  const target = approvalId ?? (life?.approval_status === 'approved' ? life.approval_id ?? null : null);
  const note = approvalId !== null
    ? null
    : lifecycle.isError
      ? REVOKE_COPY.checkFailed
      : !life
        ? REVOKE_COPY.checking
        : target === null
          ? REVOKE_COPY.noApproval
          : life.outreach_status && life.outreach_status !== 'none' && life.outreach_status !== 'queued'
            ? REVOKE_COPY.outreachStarted
            : null;
  const ready = target !== null && note === null;

  const cancel = () => {
    onClose();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  };
  const cancelRef = useRef(cancel);
  useEffect(() => {
    cancelRef.current = cancel;
  });
  useEffect(() => {
    liveRef.current = true;
    const pop = pushEscapeLayer(() => {
      cancelRef.current();
      return true;
    });
    return () => {
      liveRef.current = false;
      pop();
    };
  }, []);
  useEffect(() => {
    if (ready) fieldRef.current?.focus();
  }, [ready]);

  const send = (approval: string, rationale: string, fingerprint: string) => {
    if (inFlight.current || !liveRef.current) return;
    inFlight.current = true;
    setFailure(null);
    revoke.mutate(
      { decision: 'revoke', borrowerId, approvalId: approval, rationale, requestId: requestIds.idFor(fingerprint) },
      {
        onSettled: () => {
          inFlight.current = false;
        },
        onSuccess: (result) => {
          if (!result.revoked) return;
          requestIds.settle(fingerprint);
          dropOverride(store, borrowerId);
          toast.info(`Approval revoked for ${borrowerId}`, {
            auditEventId: result.audit_event_id,
            detail: result.released_assignment_id ? 'Its unworked assignment was released.' : null,
          });
          onClose();
          requestAnimationFrame(() => document.getElementById(`lead-row-toggle-${borrowerId}`)?.focus());
        },
        onError: (error) => {
          // The same request id on a 429's Retry: the intent is unchanged (unsettled).
          loadFailureCopy()
            .then((module) => module.describeRevokeFailure(
              error,
              error instanceof ApiError
                ? { status: error.status, reason: error.reason, message: error.message, retryAfterMs: error.retryAfterMs }
                : null,
              () => send(approval, rationale, fingerprint),
              failureDeps,
            ))
            .then((copy) => copy, () => FAILED_FALLBACK)
            .then((copy) => {
              if (copy && liveRef.current) setFailure(copy);
            });
        },
      },
    );
  };

  const submit = () => {
    if (target === null || note !== null || failure?.final) return;
    const rationale = reason.trim();
    if (rationale.length === 0) {
      fieldRef.current?.focus();
      return;
    }
    send(target, rationale, intentFingerprint(borrowerId, target, rationale));
  };

  return (
    <form
      className="decision-panel decision-panel--inline revoke-panel"
      aria-labelledby={titleId}
      noValidate
      data-testid="revoke-approval-panel"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div>
        <SurfaceTitle level={3} id={titleId}>{`Revoke the approval for ${borrowerId}?`}</SurfaceTitle>
        <div className="muted fs-12">{REVOKE_COPY.consequence}</div>
      </div>
      {note && (
        <p role={note === REVOKE_COPY.checkFailed ? 'alert' : 'status'} className="revoke-panel__note" data-testid="revoke-approval-note">
          {note}
        </p>
      )}
      {ready && (
        <label className="decision-panel__field decision-panel__field--wide">
          <span className="field__label">Reason for revoking</span>
          <textarea
            ref={fieldRef}
            value={reason}
            required
            maxLength={500}
            aria-invalid={failure !== null || undefined}
            aria-describedby={hintId}
            aria-errormessage={failure ? `${hintId}-failure` : undefined}
            onChange={(event) => setReason(event.target.value)}
            data-testid="revoke-approval-reason"
          />
          <span id={hintId} className="muted fs-11">{REVOKE_COPY.hint}</span>
        </label>
      )}
      <div className="decision-panel__actions">
        <Button type="button" variant="ghost" size="sm" onClick={cancel}>Cancel</Button>
        {ready && !failure?.final && (
          <Button
            type="submit"
            variant="danger"
            size="sm"
            aria-disabled={reason.trim().length === 0 || undefined}
            loading={revoke.isPending}
            data-testid="revoke-approval-confirm"
          >
            Revoke approval
          </Button>
        )}
      </div>
      {failure && (
        <p id={`${hintId}-failure`} role="alert" className="revoke-panel__failure" data-testid="revoke-approval-failure">
          {failure.text}
        </p>
      )}
    </form>
  );
}
