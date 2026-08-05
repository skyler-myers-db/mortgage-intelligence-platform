import { useRef } from 'react';
import { Icon } from '../Icon';
import { Button } from '../Primitives';

/**
 * ApprovalBanner — prototype `.approval` BEM. Gradient amber banner with
 * shield icon, title + sub copy, Approve + Reject buttons. Nothing is ever
 * sent without the operator clicking Approve.
 *
 * R5-11 (2026-04-23): busy-state hardening. A rapid double-click used to
 * fire `onApprove()` twice before the caller's async write completed —
 * two POSTs, two audit rows. The banner now:
 *   - accepts `isSubmitting` from the caller (preferred),
 *   - also holds an internal synchronous latch for callers that pass a
 *     fire-and-forget handler,
 *   - disables both buttons during submission and sets `aria-busy` on
 *     the region so screen readers announce the transient state.
 */

interface ApprovalBannerProps {
  text?: string;
  count?: number;
  onApprove?: () => void | Promise<void>;
  onReject?: () => void | Promise<void>;
  approveLabel?: string;
  rejectLabel?: string;
  disabled?: boolean;
  approveDisabled?: boolean;
  rejectDisabled?: boolean;
  /**
   * Caller-controlled in-flight flag. When true, both buttons disable and
   * the region flips `aria-busy`. Leaves the internal latch as a fallback
   * for callers that don't thread their own async state through.
   */
  isSubmitting?: boolean;
}

export function ApprovalBanner({
  text,
  count = 1,
  onApprove,
  onReject,
  approveLabel = 'Approve outreach',
  rejectLabel = 'Reject',
  disabled,
  approveDisabled,
  rejectDisabled,
  isSubmitting,
}: ApprovalBannerProps) {
  const sub =
    text ?? `${count} borrower${count === 1 ? '' : 's'} pending review.`;
  // Synchronous latch — flips before the async handler awaits, so a
  // double-click within the same microtask can't fire two POSTs even
  // when the caller doesn't pass `isSubmitting`.
  const inFlightRef = useRef<boolean>(false);
  const busy = Boolean(isSubmitting);

  const guard = (fn?: () => void | Promise<void>) => async () => {
    if (!fn) return;
    if (inFlightRef.current || busy) return;
    inFlightRef.current = true;
    try {
      await fn();
    } finally {
      inFlightRef.current = false;
    }
  };

  const buttonsDisabled = Boolean(disabled) || busy;

  return (
    <div
      className="approval"
      role="region"
      aria-label="Approval queue"
      aria-busy={busy}
    >
      <div className="approval__ico"><Icon name="shield" size={16} /></div>
      <div className="approval__body">
        <div className="approval__title">Human approval required before outreach</div>
        <div className="approval__sub">{sub}</div>
      </div>
      <div className="approval__actions">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void guard(onReject)()}
          icon="cross"
          disabled={buttonsDisabled || Boolean(rejectDisabled)}
        >
          {rejectLabel}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => void guard(onApprove)()}
          icon="check"
          disabled={buttonsDisabled || Boolean(approveDisabled)}
        >
          {busy ? 'Submitting…' : approveLabel}
        </Button>
      </div>
    </div>
  );
}
