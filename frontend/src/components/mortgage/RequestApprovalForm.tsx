/**
 * Request approval (audit flow-02 / shell-06, D-approval-flow-c record steps
 * 15-18; deviation:request-approval). A signed-in non-approver asks an
 * approver to review named borrowers, with a governed "Reason for the
 * request" (owner ruling O3: a batch-scoped decision rationale, never a
 * borrower note; the wire field is `rationale`). Lazy: the row's and the
 * toolbar's Request approval load it on the explicit open.
 *
 * Nothing is drafted or sent: the request never calls /outreach/draft, and
 * an approver reviews each borrower's copy before anything is approved.
 * Pessimistic: the form closes, the selection drops the requested ids and
 * the success toast (with its APPROVAL_REQUESTED audit link) shows only after
 * the 200; the row chips follow from the active refetch of the list. Every
 * failure keeps the form and the text, with copy that never claims a record
 * the server did not write (O1 / W5d C3, C4). A selection over the server's
 * per-request cap (500) is refused here, before any POST: the schema 422 it
 * would draw reads as "Shorten the reason", which is the wrong guidance.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { approvalRequestFailure, type ApprovalRequestFailure } from '../../lib/apiClients/approvalRequests';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { formatCount } from '../../lib/formatters';
import { useCreateApprovalRequest } from '../../lib/mutations/approvalRequests';
import { sessionQueryOptions } from '../../lib/sessionQuery';
import { toast } from '../../lib/toast';
import type { ApprovalRequestCreated, ApprovalRequestSkip, ApprovalRequestSkipReason } from '../../types/approvalRequests';
import { Button, SurfaceTitle } from '../Primitives';
import { REQUEST_REASON_LABEL } from './approvalRequestCopy';
import { describeWriteFailure } from './leadWriteFailureToast';
import './ApprovalRequests.css';

export const REQUEST_REASON_MAX_LENGTH = 500;
/** backend/schemas/approval_request.py MAX_APPROVAL_REQUEST_BORROWERS (the create body's maxItems). */
export const MAX_APPROVAL_REQUEST_BORROWERS = 500;
export const REQUEST_OVER_CAP =
  `A request names at most ${MAX_APPROVAL_REQUEST_BORROWERS} borrowers. Select fewer to request approval.`;
const REASON_HINT = 'Say why these borrowers; leave out personal details.';
const NOTHING_SENT = "Nothing is sent. An approver reviews each borrower's copy before anything is approved.";

/** Fixed copy per classified failure; `other` reads describeApiError's body instead. */
export const REQUEST_FAILURE_COPY: Readonly<Record<Exclude<ApprovalRequestFailure, 'other'>, string>> = {
  actor_changed: 'The signed-in user changed. Nothing was recorded.',
  governed: 'The reason was refused by the governed text policy, and the refusal was recorded. '
    + 'Rewrite it without personal details or protected-class language.',
  invalid: 'Shorten the reason and leave out personal details.',
  approver: 'You can approve these borrowers yourself; nothing was recorded.',
  key_conflict: 'This request no longer matches the one first sent. Send it again.',
  none_requestable: 'None of these borrowers can be requested: each is already decided, already requested or '
    + 'not contactable. The attempt is recorded in the audit ledger.',
};

const SKIP_WORDS: Readonly<Record<ApprovalRequestSkipReason, string>> = {
  already_requested: 'already requested',
  already_decided: 'already decided',
  not_contactable: 'not contactable',
  not_found: 'not found',
};

/** "2 skipped: 1 already requested, 1 not contactable." (null when none was skipped). */
export function skippedSummary(skipped: readonly ApprovalRequestSkip[]): string | null {
  if (skipped.length === 0) return null;
  const counts = new Map<ApprovalRequestSkipReason, number>();
  for (const skip of skipped) counts.set(skip.reason, (counts.get(skip.reason) ?? 0) + 1);
  const parts = (Object.keys(SKIP_WORDS) as ApprovalRequestSkipReason[])
    .filter((reason) => counts.has(reason))
    .map((reason) => `${formatCount(counts.get(reason) ?? 0)} ${SKIP_WORDS[reason]}`);
  return `${formatCount(skipped.length)} skipped: ${parts.join(', ')}.`;
}

function borrowerNoun(count: number): string {
  return `${formatCount(count)} ${count === 1 ? 'borrower' : 'borrowers'}`;
}

export interface RequestApprovalFormProps {
  borrowerIds: readonly string[];
  /** Cancel or Escape: the parent closes the form (focus returns to the opener here). */
  onCancel: () => void;
  /** After the 200 only: the parent closes the form and drops these ids from the selection. */
  onRequested: (created: ApprovalRequestCreated) => void;
}

export function RequestApprovalForm({ borrowerIds, onCancel, onRequested }: RequestApprovalFormProps) {
  const queryClient = useQueryClient();
  const create = useCreateApprovalRequest(queryClient);
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<string | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);
  // The opener (the row's or the toolbar's Request approval) gets focus back.
  const [opener] = useState(() => (typeof document === 'undefined' ? null : document.activeElement));
  const titleId = useId();
  const hintId = useId();
  const failureId = useId();
  const capId = useId();
  const blank = reason.trim().length === 0;
  const overCap = borrowerIds.length > MAX_APPROVAL_REQUEST_BORROWERS;

  const cancel = () => {
    onCancel();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  };
  const cancelRef = useRef(cancel);
  useEffect(() => {
    cancelRef.current = cancel;
  });
  useEffect(() => {
    fieldRef.current?.focus();
    return pushEscapeLayer(() => {
      cancelRef.current();
      return true;
    });
  }, []);

  const fail = (error: unknown) => {
    const kind = approvalRequestFailure(error);
    if (kind === 'approver') void queryClient.invalidateQueries({ queryKey: sessionQueryOptions().queryKey });
    if (kind !== 'other') {
      setFailure(REQUEST_FAILURE_COPY[kind]);
      return;
    }
    void describeWriteFailure(error, 'the approval request').then((sentence) => {
      setFailure(sentence ?? 'The approval request could not be sent.');
    });
  };

  // A synchronous latch: isPending is published on a later task, so a second
  // submit inside that window would otherwise send a second POST.
  const inFlight = useRef(false);
  const submit = () => {
    if (inFlight.current || create.isPending || overCap) return;
    if (blank) {
      fieldRef.current?.focus();
      return;
    }
    setFailure(null);
    inFlight.current = true;
    create.mutate({ borrowerIds, rationale: reason.trim() }, {
      onSettled: () => {
        inFlight.current = false;
      },
      onSuccess: (created) => {
        toast.success(`Approval requested for ${borrowerNoun(created.requested.length)}`, {
          auditEventId: created.audit_event_id,
          detail: skippedSummary(created.skipped),
        });
        onRequested(created);
      },
      onError: fail,
    });
  };

  return (
    <form
      className="decision-panel decision-panel--inline approval-requests__form"
      aria-labelledby={titleId}
      // The empty submit is ours to answer (focus to the field), not the browser's bubble.
      noValidate
      data-testid="request-approval-form"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div>
        <SurfaceTitle level={3} id={titleId}>Request approval for {borrowerNoun(borrowerIds.length)}</SurfaceTitle>
        <div className="muted fs-12">An approver decides; you cannot approve these yourself.</div>
      </div>
      <label className="decision-panel__field decision-panel__field--wide">
        <span className="field__label" id={`${hintId}-label`}>{REQUEST_REASON_LABEL}</span>
        {/* Named by the label text alone: the wrapping <label> also holds the
            hint, which would otherwise be read twice, in the name and again
            as the description (virtual screen-reader pass, form-validation.txt). */}
        <textarea
          ref={fieldRef}
          value={reason}
          required
          maxLength={REQUEST_REASON_MAX_LENGTH}
          aria-labelledby={`${hintId}-label`}
          aria-invalid={failure !== null || undefined}
          aria-describedby={[hintId, overCap && capId, failure && failureId].filter(Boolean).join(' ')}
          onChange={(event) => setReason(event.target.value)}
          data-testid="request-approval-reason"
        />
        <span id={hintId} className="muted fs-11">{REASON_HINT}</span>
      </label>
      <div className="decision-panel__actions">
        <Button type="button" variant="ghost" size="sm" onClick={cancel}>Cancel</Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          // aria-disabled until a reason is written (an empty submit moves focus to the field), and over the cap.
          aria-disabled={blank || overCap || undefined}
          aria-describedby={overCap ? capId : undefined}
          loading={create.isPending}
          data-testid="request-approval-send"
        >
          Send request
        </Button>
      </div>
      <p className="muted fs-12 approval-requests__note">{NOTHING_SENT}</p>
      {overCap && (
        <p id={capId} className="approval-requests__failure" data-testid="request-approval-over-cap">{REQUEST_OVER_CAP}</p>
      )}
      {failure && (
        <p id={failureId} role="alert" className="approval-requests__failure" data-testid="request-approval-failure">
          {failure}
        </p>
      )}
    </form>
  );
}
