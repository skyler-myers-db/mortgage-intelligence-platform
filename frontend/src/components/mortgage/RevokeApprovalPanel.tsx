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
 * typed reason kept, in closed copy that claims a record only when one was
 * written (owner ruling O1, W5e C9); the raw error text is never shown.
 */
import { useContext, useEffect, useId, useRef, useState, type ContextType } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { RATIONALE_GOVERNED_REFUSAL } from '../../lib/apiClients/approvalRequests';
import { isRevokeRefusalReason } from '../../lib/apiClients/outreachRevoke';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { useRevokeApproval } from '../../lib/mutations/outreach';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import { queryKeys } from '../../lib/queryKeys';
import { toast } from '../../lib/toast';
import { AppStoreContext } from '../appStore';
import { Button, SurfaceTitle } from '../Primitives';
import { REVOKE_REFUSAL_COPY } from './DecisionReceipt.copy';
import { describeWriteFailure, toastWriteFailure } from './leadWriteFailureToast';
import './RevokeApprovalPanel.css';

export const REVOKE_REASON_MAX_LENGTH = 500;
/** NB-1's fixed, unaudited 422 (backend/api/outreach_revoke.py REVOKE_NOT_RECORDED). */
export const REVOKE_NOT_RECORDED = 'The request could not be recorded.';

export const REVOKE_COPY = {
  consequence: 'The borrower returns to pending. Nothing is sent; an unworked assignment is released. '
    + 'The approval stays in the audit ledger.',
  hint: 'Leave out personal details.',
  noApproval: 'This borrower has no current approval to revoke.',
  outreachStarted: 'Outreach has started for this borrower, so the approval can no longer be revoked.',
  checking: 'Checking the current approval…',
  checkFailed: "Couldn't check this borrower's approval. Close and try again.",
  actorChanged: 'The signed-in user changed. Nothing was recorded.',
  governed: 'The reason was refused by the governed text policy, and the refusal was recorded. '
    + 'Rewrite it without personal details or protected-class language.',
  notRecorded: 'Nothing was recorded: the audit ledger could not record this revoke. '
    + 'Rewrite the reason without personal details, then try again.',
  invalid: 'Shorten the reason and leave out personal details.',
  failed: "Couldn't revoke the approval",
} as const;

/** The inline sentence for a failed revoke, or null when the 429 toast carries it. */
export function revokeFailureCopy(error: unknown): { text: string; final: boolean } | 'toast' | 'describe' {
  if (!(error instanceof ApiError)) return 'describe';
  if (error.reason === 'actor_changed') return { text: REVOKE_COPY.actorChanged, final: false };
  if (error.status === 409 && isRevokeRefusalReason(error.reason)) {
    return { text: REVOKE_REFUSAL_COPY[error.reason], final: error.reason === 'campaign_bound' };
  }
  if (error.status === 422) {
    if (error.message === RATIONALE_GOVERNED_REFUSAL) return { text: REVOKE_COPY.governed, final: false };
    if (error.message === REVOKE_NOT_RECORDED) return { text: REVOKE_COPY.notRecorded, final: false };
    return { text: REVOKE_COPY.invalid, final: false };
  }
  if (error.status === 429 && (error.retryAfterMs ?? 0) > 0) return 'toast';
  return 'describe';
}

export interface RevokeApprovalPanelProps {
  borrowerId: string;
  /** The approval to revoke, when the opener knows it (the receipt); null reads the lifecycle on open. */
  approvalId: string | null;
  /** Cancel, Escape or a recorded revoke: the parent closes the panel. */
  onClose: () => void;
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

export default function RevokeApprovalPanel({ borrowerId, approvalId, onClose }: RevokeApprovalPanelProps) {
  const queryClient = useQueryClient();
  const revoke = useRevokeApproval(queryClient);
  const requestIds = useIntentRequestIds();
  const store = useContext(AppStoreContext);
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<{ text: string; final: boolean } | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);
  const inFlight = useRef(false);
  const liveRef = useRef(true);
  const [opener] = useState(() => (typeof document === 'undefined' ? null : document.activeElement));
  const titleId = useId();
  const hintId = useId();
  const failureId = useId();
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
  const blocked = approvalId !== null || !life
    ? null
    : target === null
      ? REVOKE_COPY.noApproval
      : life.outreach_status && life.outreach_status !== 'none' && life.outreach_status !== 'queued'
        ? REVOKE_COPY.outreachStarted
        : null;
  const ready = target !== null && blocked === null;

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
          const copy = revokeFailureCopy(error);
          if (copy === 'toast') {
            // The same request id on the retry (the intent is unchanged); inert until the countdown ends.
            toastWriteFailure(REVOKE_COPY.failed, error, 'the revoke', () => send(approval, rationale, fingerprint));
            return;
          }
          if (copy !== 'describe') {
            setFailure(copy);
            return;
          }
          void describeWriteFailure(error instanceof ApiError ? error : null, 'the revoke').then((sentence) => {
            if (liveRef.current) setFailure({ text: sentence ?? `${REVOKE_COPY.failed}.`, final: false });
          });
        },
      },
    );
  };

  const submit = () => {
    if (target === null || blocked !== null || failure?.final) return;
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
      {approvalId === null && lifecycle.isPending && (
        <p role="status" className="muted fs-12 revoke-panel__note">{REVOKE_COPY.checking}</p>
      )}
      {approvalId === null && lifecycle.isError && (
        <p role="alert" className="revoke-panel__failure">{REVOKE_COPY.checkFailed}</p>
      )}
      {blocked && <p role="status" className="revoke-panel__note" data-testid="revoke-approval-blocked">{blocked}</p>}
      {ready && (
        <label className="decision-panel__field decision-panel__field--wide">
          <span className="field__label">Reason for revoking</span>
          <textarea
            ref={fieldRef}
            value={reason}
            required
            maxLength={REVOKE_REASON_MAX_LENGTH}
            aria-invalid={failure !== null || undefined}
            aria-describedby={failure ? `${hintId} ${failureId}` : hintId}
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
        <p id={failureId} role="alert" className="revoke-panel__failure" data-testid="revoke-approval-failure">
          {failure.text}
        </p>
      )}
    </form>
  );
}
