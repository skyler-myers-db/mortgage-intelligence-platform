import { useRef, useState } from 'react';
import { ApiError, api } from '../../lib/api';
import { Icon } from '../Icon';

/**
 * GenieAnswerFeedback — thumbs-up / thumbs-down on a Genie answer with an
 * optional short comment. Submits to POST /api/genie/feedback, which writes a
 * governed audit row.
 *
 * Contract notes:
 *   - Requires both conversation_id and message_id (the audit key). When
 *     either is missing the whole control renders nothing — feedback that
 *     can't be attributed to a message is dropped rather than shown.
 *   - The comment is capped at 280 chars and the placeholder warns against
 *     names / PII. The backend rejects PII with 422; we surface that
 *     `detail` inline but NEVER echo the rejected comment back as a quoted
 *     string (that would re-surface the PII the backend just refused).
 *   - 415 (wrong content-type) / 5xx surface a generic inline error.
 *   - The submit handler is async-latched via a ref so a double-click or a
 *     second vote while a request is in flight cannot fire two POSTs.
 *   - On success the control locks to a subtle "Feedback recorded" state and
 *     both vote buttons disable. There is deliberately NO un-vote flow.
 */

const MAX_COMMENT = 280;

type Vote = 'up' | 'down';

interface GenieAnswerFeedbackProps {
  conversationId?: string | null;
  messageId?: string | null;
}

export function GenieAnswerFeedback({
  conversationId,
  messageId,
}: GenieAnswerFeedbackProps) {
  const [comment, setComment] = useState('');
  const [pending, setPending] = useState<Vote | null>(null);
  const [recorded, setRecorded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Async latch: guards against a double-submit before React re-renders the
  // disabled state. Mirrors the approval handler latch pattern.
  const inFlightRef = useRef(false);

  // Feedback needs a message to attach to. Without the audit key there is
  // nothing to record, so render nothing rather than a dead control.
  if (!conversationId || !messageId) return null;

  const submit = async (helpful: boolean) => {
    if (inFlightRef.current || recorded) return;
    inFlightRef.current = true;
    setPending(helpful ? 'up' : 'down');
    setError(null);
    const trimmed = comment.trim();
    try {
      await api.genieFeedback({
        conversation_id: conversationId,
        message_id: messageId,
        helpful,
        // Only send a comment when the user typed one — the field is optional.
        ...(trimmed.length > 0 ? { comment: trimmed } : {}),
      });
      setRecorded(true);
    } catch (err) {
      // 422 → surface the backend detail (e.g. "Comment appears to contain
      // personal data."). We show the detail message only, never the
      // rejected comment text. 415 / 5xx → generic copy.
      if (err instanceof ApiError && err.status === 422) {
        setError(err.message || 'That comment could not be accepted.');
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
          <Icon name="up" size={12} />
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-feedback__vote"
          onClick={() => submit(false)}
          disabled={pending !== null}
          aria-label="Mark this answer not helpful"
          data-testid="genie-feedback-down"
        >
          <Icon name="down" size={12} />
        </button>
      </div>
      <textarea
        className="genie-feedback__comment"
        value={comment}
        maxLength={MAX_COMMENT}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Optional — what worked or what was off? Do not include names or personal details."
        aria-label="Optional feedback comment (do not include names or personal details)"
        rows={2}
        disabled={pending !== null}
      />
      {error && (
        <div className="genie-feedback__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
