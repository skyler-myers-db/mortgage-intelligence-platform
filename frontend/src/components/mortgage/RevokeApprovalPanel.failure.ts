/**
 * Why a revoke failed, in closed copy (audit flow-v2; owner ruling O1, W5e
 * C9). Loaded by RevokeApprovalPanel only when a revoke fails, so the copy
 * and the refusal table never ride the panel's own chunk.
 *
 *  - a 409 reads its `reason` through REVOKE_REFUSAL_COPY (campaign_bound is
 *    final in v1: Confirm goes);
 *  - 'the refusal was recorded' ONLY for the governed 422 (its detail is
 *    lib/apiClients/approvalRequests' RATIONALE_GOVERNED_REFUSAL: a
 *    GOVERNED_TEXT_REFUSED row was committed);
 *  - NB-1's unaudited REVOKE_NOT_RECORDED 422 says nothing was recorded;
 *  - any other 422 asks for a shorter reason, claiming no record;
 *  - actor_changed says the user changed and nothing was recorded;
 *  - a 429 with a Retry-After is the shared toast, whose Retry re-sends the
 *    SAME request (the caller's `retry`);
 *  - anything else is describeApiError's sentence. Never the raw message.
 *
 * Bundle (W5e budget): this module imports no shared chunk's module. The
 * refusal copy and the governed detail live here, not in DecisionReceipt.copy
 * or lib/apiClients/approvalRequests, the panel hands in the failed request's
 * facts (not the ApiError class: lib/apiTransport sits in a shared initial
 * chunk), and the shell hands in the toast functions (RevokeFailureDeps).
 * Each such import split a shared chunk and added its name to every route's
 * preload list in the entry chunk.
 */
import type { RevokeRefusalReason } from '../../lib/apiClients/outreachRevoke';
import { REVOKE_CAMPAIGN_BOUND } from './LeadTable.constants';
import type { WriteFailureToasts } from './leadWriteFailureToast';

/** The revoke route's governed-text 422 (= approvalRequests.RATIONALE_GOVERNED_REFUSAL; a unit test pins it). */
export const REVOKE_GOVERNED_REFUSAL = 'rationale failed the governed text policy';
/** NB-1's fixed, unaudited 422 (backend/api/outreach_revoke.py REVOKE_NOT_RECORDED). */
export const REVOKE_NOT_RECORDED = 'The request could not be recorded.';

/** The 409 `reason` codes (backend/services/outreach_revoke.REVOKE_REFUSED_DETAIL). */
export const REVOKE_REFUSAL_REASONS: readonly RevokeRefusalReason[] = [
  'not_current',
  'campaign_bound',
  'dispositioned',
  'outcome_recorded',
  'delivered',
  'assignment_worked',
];

/** One closed sentence per revoke 409 `reason`; the server's detail is never shown, nothing was written. */
export const REVOKE_REFUSAL_COPY: Readonly<Record<RevokeRefusalReason, string>> = {
  not_current: "This approval is no longer the borrower's current decision. Nothing was revoked; reload to see the latest.",
  campaign_bound: REVOKE_CAMPAIGN_BOUND,
  dispositioned: 'A call has been logged since the approval, so it can no longer be revoked.',
  outcome_recorded: 'A lead outcome has been recorded since the approval, so it can no longer be revoked.',
  delivered: "This approval's outreach has been delivered, so it can no longer be revoked.",
  assignment_worked: 'The loan officer has already worked this lead, so the approval can no longer be revoked.',
};

export const REVOKE_FAILURE_COPY = {
  actorChanged: 'The signed-in user changed. Nothing was recorded.',
  governed: 'The reason was refused by the governed text policy, and the refusal was recorded. '
    + 'Rewrite it without personal details or protected-class language.',
  notRecorded: 'Nothing was recorded: the audit ledger could not record this revoke. '
    + 'Rewrite the reason without personal details, then try again.',
  invalid: 'Shorten the reason and leave out personal details.',
  failed: "Couldn't revoke the approval",
} as const;

/** The fixed sentence for a failure no other line explains. */
export const REVOKE_FAILED_LINE = `${REVOKE_FAILURE_COPY.failed}.`;

export interface RevokeFailureCopy {
  text: string;
  /** No Confirm after it (campaign_bound in v1). */
  final: boolean;
}

/** The queue's write-failure toast and sentence (leadWriteFailureToast), handed in by the shell. */
export type RevokeFailureDeps = WriteFailureToasts;

/** What the panel read off a failed request's ApiError; null for anything else. */
export interface RevokeFailureFacts {
  status: number | null;
  reason: string | null;
  message: string;
  retryAfterMs: number | null;
}

function isRevokeRefusalReason(value: unknown): value is RevokeRefusalReason {
  return typeof value === 'string' && (REVOKE_REFUSAL_REASONS as readonly string[]).includes(value);
}

/** The inline sentence for a classified failure; 'toast' for a 429 with a wait; 'describe' otherwise. */
export function revokeFailureCopy(facts: RevokeFailureFacts | null): RevokeFailureCopy | 'toast' | 'describe' {
  if (facts === null) return 'describe';
  if (facts.reason === 'actor_changed') return { text: REVOKE_FAILURE_COPY.actorChanged, final: false };
  if (facts.status === 409 && isRevokeRefusalReason(facts.reason)) {
    return { text: REVOKE_REFUSAL_COPY[facts.reason], final: facts.reason === 'campaign_bound' };
  }
  if (facts.status === 422) {
    if (facts.message === REVOKE_GOVERNED_REFUSAL) return { text: REVOKE_FAILURE_COPY.governed, final: false };
    if (facts.message === REVOKE_NOT_RECORDED) return { text: REVOKE_FAILURE_COPY.notRecorded, final: false };
    return { text: REVOKE_FAILURE_COPY.invalid, final: false };
  }
  if (facts.status === 429 && (facts.retryAfterMs ?? 0) > 0) return 'toast';
  return 'describe';
}

/**
 * The inline copy for a failed revoke, or null when the shared 429 toast
 * carries it (its Retry calls `retry`, which re-sends the same request id).
 */
export function describeRevokeFailure(
  error: unknown,
  facts: RevokeFailureFacts | null,
  retry: () => void,
  deps: RevokeFailureDeps,
): Promise<RevokeFailureCopy | null> {
  const copy = revokeFailureCopy(facts);
  if (copy === 'toast') {
    deps.toastWriteFailure(REVOKE_FAILURE_COPY.failed, error, 'the revoke', retry);
    return Promise.resolve(null);
  }
  if (copy !== 'describe') return Promise.resolve(copy);
  // A non-API failure never reaches describeWriteFailure, whose fallback is the raw message.
  return (facts === null ? Promise.resolve(null) : deps.describeWriteFailure(error, 'the revoke'))
    .then((sentence) => ({ text: sentence ?? REVOKE_FAILED_LINE, final: false }));
}
