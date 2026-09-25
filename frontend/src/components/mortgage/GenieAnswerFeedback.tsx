import { useEffect, useRef, useState } from 'react';
import { ApiError, api } from '../../lib/api';
import {
  beginGenieVote,
  genieVoteKey,
  settleGenieVote,
  useGenieVote,
  type GenieVoteDirection,
} from '../../lib/genieAnswerMemory';
import { Icon, ThumbsDown, ThumbsUp } from '../Icon';

/**
 * GenieAnswerFeedback — thumbs-up / thumbs-down on a Genie answer with an
 * a governed audit row. The app intentionally collects only the binary vote;
 * free-text comments can carry borrower details and are not accepted.
 *
 * Contract notes:
 *   - Requires both conversation_id and message_id (the audit key). When
 *     either is missing the whole control renders nothing — feedback that
 *     can't be attributed to a message is dropped rather than shown.
 *   - 415 (wrong content-type) / 5xx surface a generic inline error.
 *   - The submit handler is latched by the vote memory (lib/genieAnswerMemory)
 *     so a double-click or a second vote while a request is in flight cannot
 *     fire two POSTs, in this mount of the answer or any later one.
 *   - On success the control locks to a subtle "Feedback recorded" state and
 *     both vote buttons disable. There is deliberately NO un-vote flow, and a
 *     recorded vote is never offered again.
 */

type Vote = GenieVoteDirection;

type FeedbackOutcome = { ok: true } | { ok: false; message: string };

/**
 * POST one vote. Never rejects: a failure becomes its fixed user-facing
 * line. Outside the component so the submit handler needs no try statement
 * (audit 2026-09-21 `runtime-03`: a try/finally stops the React Compiler).
 */
function recordGenieFeedback(body: Parameters<typeof api.genieFeedback>[0]): Promise<FeedbackOutcome> {
  return api.genieFeedback(body).then(
    (): FeedbackOutcome => ({ ok: true }),
    (err: unknown): FeedbackOutcome => {
      // A policy rejection can still return 422 for a stale client payload.
      // Surface only the fixed backend detail.
      if (err instanceof ApiError && err.status === 422) {
        return { ok: false, message: err.message || 'Feedback could not be recorded.' };
      }
      if (err instanceof ApiError && err.status === 409) {
        return {
          ok: false,
          message: 'Feedback is still being processed or could not be reconciled. Retry to safely confirm the same vote.',
        };
      }
      return { ok: false, message: 'Feedback could not be recorded. Please try again.' };
    },
  );
}

export const GENIE_FEEDBACK_RECORDED = 'Feedback recorded';

interface GenieAnswerFeedbackProps {
  conversationId?: string | null;
  messageId?: string | null;
  /** Speak the done state through the surface's ONE persistent announcer
   *  (audit `a11y-06`): the done label is not a live region of its own. */
  onAnnounce?: (text: string) => void;
}

export function GenieAnswerFeedback({
  conversationId,
  messageId,
  onAnnounce,
}: GenieAnswerFeedbackProps) {
  const voteKey = conversationId && messageId ? genieVoteKey(conversationId, messageId) : null;
  // The vote lives in lib/genieAnswerMemory, not here (audit genie-08 item
  // 3): collapsing the turn, a remount or a return to the page shows the same
  // pending or recorded vote, and a retry reuses the same request id.
  const vote = useGenieVote(voteKey);
  const recorded = vote.status === 'recorded';
  // A failure is shown only under the answer it happened on.
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  // The answer this control shows now: a vote that settles after the ids
  // changed under it moves no focus and shows no error here.
  const shownKeyRef = useRef(voteKey);
  // The vote button unmounts on success; focus follows to the done label
  // only when it was on a vote button (GenieRefusalCard does the same).
  const rowRef = useRef<HTMLDivElement | null>(null);
  const doneRef = useRef<HTMLSpanElement | null>(null);
  const focusDoneRef = useRef(false);

  useEffect(() => {
    shownKeyRef.current = voteKey;
  }, [voteKey]);

  useEffect(() => {
    if (!recorded || !focusDoneRef.current) return;
    focusDoneRef.current = false;
    doneRef.current?.focus();
  }, [recorded]);

  // Feedback needs a message to attach to. Without the audit key there is
  // nothing to record, so render nothing rather than a dead control.
  if (!conversationId || !messageId || voteKey === null) return null;

  const focusInRow = () => {
    const active = typeof document === 'undefined' ? null : document.activeElement;
    return Boolean(active && rowRef.current?.contains(active));
  };

  const submit = (helpful: boolean) => {
    const direction: Vote = helpful ? 'up' : 'down';
    // Read before the buttons disable: was the vote made with focus on it?
    const voteButtonHadFocus = focusInRow();
    // The synchronous latch: a double click, a second direction or a vote
    // already recorded (in any mount of this answer) sends nothing.
    const start = beginGenieVote(voteKey, direction, () => crypto.randomUUID());
    if (!start) return;
    setError(null);
    void recordGenieFeedback({
      conversation_id: conversationId,
      message_id: messageId,
      helpful,
      request_id: start.requestId,
    }).then((outcome) => {
      const stillShown = shownKeyRef.current === voteKey;
      if (outcome.ok && stillShown) {
        // Follow only if focus is still on the row, or was dropped to <body>
        // when the pressed button disabled; never pull it back from elsewhere.
        focusDoneRef.current = voteButtonHadFocus && (focusInRow() || document.activeElement === document.body);
      }
      // Nothing more after an actor change cleared the memory mid-vote.
      if (!settleGenieVote(voteKey, start, outcome.ok)) return;
      if (outcome.ok) onAnnounce?.(GENIE_FEEDBACK_RECORDED);
      else if (stillShown) setError({ key: voteKey, message: outcome.message });
    });
  };
  const pending = vote.status === 'pending';
  const shownError = error !== null && error.key === voteKey ? error.message : null;

  if (recorded) {
    // Not a live region (audit `a11y-06`): a region mounted already
    // populated is unreliably spoken; the surface announcer says it.
    return (
      <div className="genie-feedback genie-feedback--done">
        <Icon name="check" size={12} className="icon-accent" />
        <span ref={doneRef} className="genie-feedback__done-label" tabIndex={-1}>
          {GENIE_FEEDBACK_RECORDED}
        </span>
      </div>
    );
  }

  return (
    <div className="genie-feedback">
      <div className="genie-feedback__row" ref={rowRef}>
        <span className="genie-feedback__prompt">Was this helpful?</span>
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-feedback__vote"
          onClick={() => submit(true)}
          disabled={pending}
          aria-label="Mark this answer helpful"
          data-testid="genie-feedback-up"
        >
          <ThumbsUp size={14} />
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-feedback__vote"
          onClick={() => submit(false)}
          disabled={pending}
          aria-label="Mark this answer not helpful"
          data-testid="genie-feedback-down"
        >
          <ThumbsDown size={14} />
        </button>
      </div>
      {shownError && (
        <div className="genie-feedback__error" role="alert">
          {shownError}
        </div>
      )}
    </div>
  );
}
