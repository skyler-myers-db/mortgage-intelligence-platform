/**
 * Per-turn conversational controls shared by the floating Genie panel and
 * the `/ask-genie` thread (audit 2026-09-21 `genie-03`, client-only slice).
 *
 *   Edit        reloads the sent question into the composer. Never sends.
 *   Regenerate  asks the same question again as a NEW Genie turn. A Genie
 *               thread cannot rewrite its history, so the earlier answer
 *               stays in the transcript; the tooltip says so.
 *   Retry       the same re-ask, offered on a failed (degraded) turn.
 *   Ask again   the same re-ask, offered on a STOPPED turn: it has no answer
 *               to regenerate.
 *
 * Every re-ask goes through the caller's ordinary ask path, so it is scanned
 * by the same server-side guards as a typed question. There is no rewrite
 * and no server-side cancel here.
 *
 * `.genie__msg-actions` is a documented BEM extension of the prototype's
 * `.genie__msg` (design_files/index.html:741-760 has no controls on a
 * bubble). The buttons reuse `.btn .btn--ghost .btn--sm`.
 */

export const REGENERATE_TITLE =
  'Ask this question again as a new Genie turn. A Genie thread cannot rewrite its history, so the earlier answer stays.';

interface GenieTurnActionsProps {
  /** The question these controls act on. */
  question: string;
  /** Load `question` back into the composer. */
  onEdit?: (question: string) => void;
  /** Re-ask `question` as a new turn. */
  onRegenerate?: (question: string) => void;
  /** Re-ask a failed turn; renders a "Retry" button. */
  onRetry?: (question: string) => void;
  /** Re-ask a stopped turn (no answer exists); renders an "Ask again" button. */
  onAskAgain?: (question: string) => void;
  /** True while a turn is in flight: a second ask must not start. */
  disabled?: boolean;
  /** Why the re-ask controls are disabled, for the tooltip. */
  disabledReason?: string | null;
  /** Under a question bubble (right-aligned) or under an answer. */
  placement: 'question' | 'answer';
}

export function GenieTurnActions({
  question,
  onEdit,
  onRegenerate,
  onRetry,
  onAskAgain,
  disabled = false,
  disabledReason = null,
  placement,
}: GenieTurnActionsProps) {
  if (!question.trim()) return null;
  if (!onEdit && !onRegenerate && !onRetry && !onAskAgain) return null;
  return (
    <div
      className={`genie__msg-actions${placement === 'question' ? ' genie__msg-actions--user' : ''}`}
      role="group"
      aria-label={placement === 'question' ? 'Question actions' : 'Answer actions'}
    >
      {onEdit && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onEdit(question)}
          title="Load this question into the composer to edit it. Nothing is sent until you press Ask."
          aria-label="Edit question"
        >
          Edit
        </button>
      )}
      {onRetry && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onRetry(question)}
          disabled={disabled}
          title={disabled ? (disabledReason ?? undefined) : 'Ask this question again as a new Genie turn'}
          aria-label="Retry question"
        >
          Retry
        </button>
      )}
      {onRegenerate && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onRegenerate(question)}
          disabled={disabled}
          title={disabled ? (disabledReason ?? undefined) : REGENERATE_TITLE}
          aria-label="Regenerate answer"
        >
          Regenerate
        </button>
      )}
      {onAskAgain && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onAskAgain(question)}
          disabled={disabled}
          title={disabled ? (disabledReason ?? undefined) : 'Ask this question again as a new Genie turn'}
          aria-label="Ask this question again"
        >
          Ask again
        </button>
      )}
    </div>
  );
}
