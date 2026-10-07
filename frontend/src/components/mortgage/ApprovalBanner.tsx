import { useId, useRef } from 'react';
import { Icon } from '../Icon';
import { Button } from '../Primitives';
import { usePendingApprovalBanner } from './usePendingApprovalBanner';

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
 *
 * Audit flow-02 / shell-06 (2026-09-21): the banner used to be identity- and
 * role-blind. It now names whose approval this is ("Approving as …") and,
 * for an actor the session says cannot approve, keeps the gate VISIBLE with
 * both buttons disabled and the reason attached via `aria-describedby`. The
 * identity line is a second `.approval__sub` — no new BEM; the prototype
 * banner (design_files/index.html `.approval`) has no identity element, so
 * this is an additive departure made for the audit-trail promise.
 *
 * One approve label (audit critic-11): the primary action always reads
 * APPROVE_OUTREACH_LABEL, the word the surrounding prose uses too; the
 * approveLabel / rejectLabel overrides had no caller and are gone.
 *
 * A decision in flight from ANY surface (audit stack-09 item 5;
 * deviation:approval-pending-line): given `borrowerId`, the banner reads the
 * shared mutation cache, so an approve or reject for that borrower started
 * elsewhere (the Lead Queue row, another view of the same cache) makes it
 * busy too: aria-busy, both buttons disabled, and one role=status
 * `.approval__sub` line, 'Approval being recorded…' or 'Rejection being
 * recorded…'. Pessimistic: it never shows 'approved' before the write returns.
 */

/** The approve button's one label, everywhere the banner renders. */
export const APPROVE_OUTREACH_LABEL = 'Approve outreach';

interface ApprovalBannerProps {
  text?: string;
  count?: number;
  onApprove?: () => void | Promise<void>;
  onReject?: () => void | Promise<void>;
  disabled?: boolean;
  approveDisabled?: boolean;
  rejectDisabled?: boolean;
  /**
   * Caller-controlled in-flight flag. When true, both buttons disable and
   * the region flips `aria-busy`. Leaves the internal latch as a fallback
   * for callers that don't thread their own async state through.
   */
  isSubmitting?: boolean;
  /**
   * Non-null = the signed-in actor may not approve (see `approverGateReason`);
   * the text is the accessible reason. The server-side 403 stays the real
   * enforcement — this only stops the doomed click and explains it.
   */
  approverGate?: string | null;
  /** The signed-in actor's own forwarded identity, when the edge sent one. */
  actorEmail?: string | null;
  /** The borrower this gate decides: a decision for it in flight anywhere makes the banner busy. */
  borrowerId?: string | null;
}

const PENDING_LINE = { approve: 'Approval being recorded…', reject: 'Rejection being recorded…' } as const;

export function ApprovalBanner({
  text,
  count = 1,
  onApprove,
  onReject,
  disabled,
  approveDisabled,
  rejectDisabled,
  isSubmitting,
  approverGate = null,
  actorEmail = null,
  borrowerId = null,
}: ApprovalBannerProps) {
  const sub =
    text ?? `${count} borrower${count === 1 ? '' : 's'} pending review.`;
  // Synchronous latch — flips before the async handler awaits, so a
  // double-click within the same microtask can't fire two POSTs even
  // when the caller doesn't pass `isSubmitting`.
  const inFlightRef = useRef<boolean>(false);
  const pendingDecision = usePendingApprovalBanner(borrowerId);
  const busy = Boolean(isSubmitting) || pendingDecision !== null;
  const gated = approverGate !== null;
  const gateId = useId();

  // No try statement (audit runtime-03): React Compiler 1.0 bails out of the
  // whole component on a try/finally. The Promise executor runs `fn` now and
  // turns a synchronous throw into a rejection, so the latch is released on
  // every outcome and a failure still rejects, exactly as before.
  const guard = (fn?: () => void | Promise<void>) => (): Promise<void> => {
    if (!fn) return Promise.resolve();
    if (gated || inFlightRef.current || busy) return Promise.resolve();
    inFlightRef.current = true;
    const release = () => {
      inFlightRef.current = false;
    };
    return new Promise<void>((resolve) => {
      resolve(fn());
    }).then(release, (error: unknown) => {
      release();
      throw error;
    });
  };

  const buttonsDisabled = Boolean(disabled) || busy || gated;

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
        {gated ? (
          <div className="approval__sub" id={gateId} role="status" data-testid="approval-gate-reason">
            {approverGate}
            {actorEmail && <> · signed in as <span className="mono">{actorEmail}</span></>}
          </div>
        ) : actorEmail && (
          <div className="approval__sub" data-testid="approval-actor">
            Approving as <span className="mono">{actorEmail}</span>
          </div>
        )}
        {/* Always mounted, so the line is announced when a decision starts;
            empty, it takes no room. */}
        <div className={pendingDecision ? 'approval__sub' : 'sr-only'} role="status" data-testid="approval-pending">
          {pendingDecision ? PENDING_LINE[pendingDecision] : ''}
        </div>
      </div>
      <div className="approval__actions">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void guard(onReject)()}
          icon="cross"
          disabled={buttonsDisabled || Boolean(rejectDisabled)}
          aria-describedby={gated ? gateId : undefined}
          title={approverGate ?? undefined}
        >
          Reject
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => void guard(onApprove)()}
          icon="check"
          disabled={buttonsDisabled || Boolean(approveDisabled)}
          aria-describedby={gated ? gateId : undefined}
          title={approverGate ?? undefined}
        >
          {/* The caller's own write swaps the label; a decision from another
              surface is told by the status line, the button keeps its name. */}
          {isSubmitting ? 'Submitting…' : APPROVE_OUTREACH_LABEL}
        </Button>
      </div>
    </div>
  );
}
