import type { ToastAction } from '../../lib/toast';
import { Countdown, useSecondsUntil } from '../ui/RetryClock';

/**
 * A toast's one action (W5b shell toast platform; states-07 item 1 and
 * states-08 item 3 prerequisites; deviation:toast-actions-and-path).
 *
 * One `.btn--ghost` button, described by the toast's title, so a screen
 * reader hears "Retry, Approve failed for 3 borrowers". While a Retry-After
 * runs (`readyAt` in the future) it stays aria-disabled, never natively
 * disabled (a disabled control drops focus and leaves the tab order), its
 * click does nothing, and the shared RetryClock counts the wait down, as
 * AsyncFailure's CountdownRetry does; at zero it is live. The card runs the
 * action once and dismisses the toast (Toaster's ToastCard).
 */
export function ToastActionRow({
  action,
  readyAt,
  titleId,
  onActivate,
}: {
  action: ToastAction;
  /** Epoch ms the action is live from; null when it is live at once. */
  readyAt: number | null;
  titleId: string;
  /** `clickDetail` is the click's `detail`: 0 for Enter / Space, 1+ for a pointer. */
  onActivate: (clickDetail: number) => void;
}) {
  const waiting = useSecondsUntil(readyAt ?? 0) > 0;
  return (
    <div className="toast__actions">
      {waiting && readyAt !== null && (
        <span className="toast__wait muted">
          Try again in <Countdown until={readyAt} />
        </span>
      )}
      <button
        type="button"
        className="btn btn--ghost btn--sm toast__action"
        aria-describedby={titleId}
        aria-disabled={waiting || undefined}
        onClick={(event) => {
          if (!waiting) onActivate(event.detail);
        }}
      >
        {action.label}
      </button>
    </div>
  );
}
