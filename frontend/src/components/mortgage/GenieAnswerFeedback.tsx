import { useRef, useState } from 'react';
import { ApiError, api } from '../../lib/api';
import { Icon } from '../Icon';

/**
 * GenieAnswerFeedback — thumbs-up / thumbs-down on a Genie answer with an
 * governed audit row. The app intentionally collects only the binary vote;
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

interface GenieAnswerFeedbackProps {
  conversationId?: string | null;
  messageId?: string | null;
}

export function GenieAnswerFeedback({
  conversationId,
  messageId,
}: GenieAnswerFeedbackProps) {
  const [pending, setPending] = useState<Vote | null>(null);
  const [recorded, setRecorded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Async latch: guards against a double-submit before React re-renders the
  // disabled state. Mirrors the approval handler latch pattern.
  const inFlightRef = useRef(false);
  const requestIdsRef = useRef<Partial<Record<Vote, string>>>({});

  // Feedback needs a message to attach to. Without the audit key there is
  // nothing to record, so render nothing rather than a dead control.
  if (!conversationId || !messageId) return null;

  const submit = async (helpful: boolean) => {
    if (inFlightRef.current || recorded) return;
    inFlightRef.current = true;
    setPending(helpful ? 'up' : 'down');
    setError(null);
    const vote: Vote = helpful ? 'up' : 'down';
    const requestId = requestIdsRef.current[vote] ?? crypto.randomUUID();
    requestIdsRef.current[vote] = requestId;
    try {
      await api.genieFeedback({
        conversation_id: conversationId,
        message_id: messageId,
        helpful,
        request_id: requestId,
      });
      setRecorded(true);
    } catch (err) {
      // A policy rejection can still return 422 for a stale client payload.
      // Surface only the fixed backend detail.
      if (err instanceof ApiError && err.status === 422) {
        setError(err.message || 'Feedback could not be recorded.');
      } else {
        setError('Feedback could not be recorded. Please try again.');
      }
    } finally {
      inFlightRef.current = false;
      setPending(null);
    }
  };

  if (recorded) {
    return (
      <div className="genie-feedback genie-feedback--done" role="status">
        <Icon name="check" size={12} className="icon-accent" />
        <span className="genie-feedback__done-label">Feedback recorded</span>
      </div>
    );
  }

  return (
    <div className="genie-feedback">
      <div className="genie-feedback__row">
        <span className="genie-feedback__prompt">Was this helpful?</span>
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-feedback__vote"
          onClick={() => submit(true)}
          disabled={pending !== null}
          aria-label="Mark this answer helpful"
          data-testid="genie-feedback-up"
        >
          <Icon name="thumbup" size={14} />
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-feedback__vote"
          onClick={() => submit(false)}
          disabled={pending !== null}
          aria-label="Mark this answer not helpful"
          data-testid="genie-feedback-down"
        >
          <Icon name="thumbdown" size={14} />
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
