import { useEffect, useRef, useState } from 'react';
import { ApiError, api } from '../../lib/api';
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
 *   - The submit handler is async-latched via a ref so a double-click or a
 *     second vote while a request is in flight cannot fire two POSTs.
 *   - On success the control locks to a subtle "Feedback recorded" state and
 *     both vote buttons disable. There is deliberately NO un-vote flow.
 */

type Vote = 'up' | 'down';

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

/**
 * Votes recorded in this tab, by `conversationId:messageId`. A collapsed
 * earlier turn unmounts its answer (audit `genie-08`), so the control's own
 * state does not survive a collapse/expand; this makes sure a recorded vote
 * is never offered again. It holds opaque ids only and lives with the tab.
 */
const recordedVotes = new Set<string>();

export function __resetGenieFeedbackMemoryForTests(): void {
  recordedVotes.clear();
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
  const identity = `${conversationId ?? ''}:${messageId ?? ''}`;
  const [pending, setPending] = useState<Vote | null>(null);
  const [recorded, setRecorded] = useState(() => recordedVotes.has(identity));
  const [error, setError] = useState<string | null>(null);
  // Async latch: guards against a double-submit before React re-renders the
  // disabled state. Mirrors the approval handler latch pattern.
  const inFlightRef = useRef(false);
  const requestIdsRef = useRef<Partial<Record<Vote, string>>>({});
  const identityRef = useRef(identity);
  // The vote button unmounts on success; focus follows to the done label
  // only when it was on a vote button (GenieRefusalCard does the same).
  const rowRef = useRef<HTMLDivElement | null>(null);
  const doneRef = useRef<HTMLSpanElement | null>(null);
  const focusDoneRef = useRef(false);

  useEffect(() => {
    identityRef.current = identity;
    inFlightRef.current = false;
    requestIdsRef.current = {};
    setPending(null);
    setRecorded(recordedVotes.has(identity));
    setError(null);
  }, [identity]);

  useEffect(() => {
    if (!recorded || !focusDoneRef.current) return;
    focusDoneRef.current = false;
    doneRef.current?.focus();
  }, [recorded]);

  // Feedback needs a message to attach to. Without the audit key there is
  // nothing to record, so render nothing rather than a dead control.
  if (!conversationId || !messageId) return null;

  const focusInRow = () => {
    const active = typeof document === 'undefined' ? null : document.activeElement;
    return Boolean(active && rowRef.current?.contains(active));
  };

  const submit = (helpful: boolean) => {
    if (inFlightRef.current || recorded) return;
    inFlightRef.current = true;
    // Read before the buttons disable: was the vote made with focus on it?
    const voteButtonHadFocus = focusInRow();
    setPending(helpful ? 'up' : 'down');
    setError(null);
    const vote: Vote = helpful ? 'up' : 'down';
    const submittedIdentity = identity;
    const requestId = requestIdsRef.current[vote] ?? crypto.randomUUID();
    requestIdsRef.current[vote] = requestId;
    void recordGenieFeedback({
      conversation_id: conversationId,
      message_id: messageId,
      helpful,
      request_id: requestId,
    }).then((outcome) => {
      if (outcome.ok) recordedVotes.add(submittedIdentity);
      // A vote for an answer the control no longer shows changes nothing.
      if (identityRef.current !== submittedIdentity) return;
      if (outcome.ok) {
        // Follow only if focus is still on the row, or was dropped to <body>
        // when the pressed button disabled; never pull it back from elsewhere.
        focusDoneRef.current = voteButtonHadFocus && (focusInRow() || document.activeElement === document.body);
        setRecorded(true);
        onAnnounce?.(GENIE_FEEDBACK_RECORDED);
      } else {
        setError(outcome.message);
      }
      inFlightRef.current = false;
      setPending(null);
    });
  };

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
          disabled={pending !== null}
          aria-label="Mark this answer helpful"
          data-testid="genie-feedback-up"
        >
          <ThumbsUp size={14} />
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-feedback__vote"
          onClick={() => submit(false)}
          disabled={pending !== null}
          aria-label="Mark this answer not helpful"
          data-testid="genie-feedback-down"
        >
          <ThumbsDown size={14} />
        </button>
      </div>
      {error && (
        <div className="genie-feedback__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
