import { useEffect, useId, useRef, type RefObject } from 'react';
import type { DrawerSource } from '../AppContext';
import { descriptorFor } from '../../lib/drawerSources';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { Skeleton } from '../ui/Skeleton';
import { leadApproveReviewId } from './LeadApproveReview.ids';
import type { LeadApproveReviewPhase, LeadApproveReviewState } from './useLeadApproveReview';
import './LeadApproveReview.css';

// The approval result line renders only after an approve made through this
// review, so it ships in this chunk rather than in the table's.
export { LeadTableDecisionToast } from './LeadTableDecisionToast';

/**
 * The approve-review sheet (audit flow-03, states-06): the governed email a
 * row approval certifies, shown BEFORE the approval, inside the prototype's
 * `.approval` human-in-the-loop gate (design_files/index.html `.approval`).
 *
 * Declared departure: the prototype `.approval` is a one-line banner
 * (design_files/index.html lines 634-651: icon, title, sub, actions on one
 * row). The review needs the full subject and message on screen, so the
 * `--review` modifier stacks the body under the title and top-aligns the
 * icon (LeadApproveReview.css). The prototype's `.approval__ico`, `__body`,
 * `__title`, `__sub` and `__actions` keep their names; the review adds five
 * elements the banner has no slot for: `.approval__copy` (the draft block),
 * `__fields` and `__field-value` (channel, subject, message), `__message`
 * (the message body, line breaks kept) and `__note` (the drafting notice).
 *
 * It states the channel explicitly ("Email": the queue always drafts
 * email), the disclosure version and state, and the evidence sources the
 * draft cites. Confirm submits that exact draft; Cancel abandons it.
 */
export interface LeadApproveReviewProps {
  review: LeadApproveReviewState;
  actorEmail: string | null;
  onConfirm: () => void;
  onCancel: () => void;
  onRetryDraft: () => void;
  /**
   * Opening an evidence source from the review (the dialog moves inline
   * first). `chipIndex` names the chip, so its inline twin can hold focus.
   */
  onInspectEvidence?: (source: DrawerSource, chipIndex: number) => void;
  confirmRef?: RefObject<HTMLButtonElement | null>;
  /**
   * Asked when the draft lands: may Confirm take focus (so Enter approves)?
   * False when the reader has moved on. Default: yes.
   */
  shouldTakeFocus?: (borrowerId: string) => boolean;
  /**
   * Asked when a mounted review shows a ready draft: is this the draft
   * landing? True once per landed draft (useLeadApproveReview), so a review
   * first rendered after its draft landed still counts, and one that moved
   * or remounted does not. Default: the drafting -> ready transition only.
   */
  claimDraftLanding?: (borrowerId: string) => boolean;
}

export function LeadApproveReview({
  review,
  actorEmail,
  onConfirm,
  onCancel,
  onRetryDraft,
  onInspectEvidence,
  confirmRef,
  shouldTakeFocus,
  claimDraftLanding,
}: LeadApproveReviewProps) {
  const titleId = useId();
  const { borrowerId, phase, draft, error } = review;
  const localConfirmRef = useRef<HTMLButtonElement>(null);
  const buttonRef = confirmRef ?? localConfirmRef;
  const shouldTakeFocusRef = useRef(shouldTakeFocus);
  const claimDraftLandingRef = useRef(claimDraftLanding);
  useEffect(() => {
    shouldTakeFocusRef.current = shouldTakeFocus;
    claimDraftLandingRef.current = claimDraftLanding;
  }, [shouldTakeFocus, claimDraftLanding]);
  // The draft landed: Confirm takes focus, so Enter approves this copy.
  // Once per landed draft, never on a mount that merely shows a draft that
  // already landed: the dialog review moving into its row (to open an
  // evidence source, so the drawer is about to own focus) or an inline
  // review remounting as its row scrolls back into the virtual window.
  const previousPhaseRef = useRef<LeadApproveReviewPhase | null>(null);
  useEffect(() => {
    const previous = previousPhaseRef.current;
    previousPhaseRef.current = phase;
    if (phase !== 'ready') return;
    const claim = claimDraftLandingRef.current;
    const landed = claim ? claim(borrowerId) : previous === 'drafting';
    if (!landed) return;
    if (shouldTakeFocusRef.current && !shouldTakeFocusRef.current(borrowerId)) return;
    buttonRef.current?.focus();
  }, [phase, borrowerId, buttonRef]);
  const busy = phase === 'drafting' || phase === 'submitting';
  const showCopy = (phase === 'ready' || phase === 'submitting') && draft !== null;
  return (
    <form
      id={leadApproveReviewId(borrowerId)}
      className="approval approval--review"
      aria-labelledby={titleId}
      aria-busy={busy || undefined}
      data-testid="lead-approve-review"
      data-review-phase={phase}
      onSubmit={(event) => {
        event.preventDefault();
        onConfirm();
      }}
    >
      <div className="approval__ico" aria-hidden="true">
        <Icon name="shield" size={16} />
      </div>
      <div className="approval__body">
        <div className="approval__title" id={titleId}>
          Review the outreach before approving <span className="mono">{borrowerId}</span>
        </div>
        <div className="approval__sub">
          Channel: Email. Approving certifies this exact subject and message.
          {actorEmail && <> Approving as <span className="mono">{actorEmail}</span>.</>}
        </div>
        {phase === 'drafting' && (
          <div className="approval__copy" data-testid="lead-approve-review-drafting">
            <Skeleton width="48%" />
            <Skeleton width="92%" />
            <Skeleton width="76%" />
            <p className="approval__note">
              Generating the governed draft. Each draft is recorded in the audit log.
            </p>
          </div>
        )}
        {phase === 'error' && (
          <div className="approval__copy">
            <div className="status-callout status-callout--danger" role="alert">
              <span>{error ?? 'The governed draft could not be generated.'} Approval is unavailable until a draft loads.</span>
            </div>
            <div className="chip-row">
              <Button type="button" size="sm" icon="play" onClick={onRetryDraft}>
                Generate draft again
              </Button>
            </div>
          </div>
        )}
        {showCopy && draft && (
          <div className="approval__copy">
            <dl className="approval__fields">
              <div>
                <dt className="field__label">Channel</dt>
                <dd className="approval__field-value" data-testid="lead-approve-review-channel">Email</dd>
              </div>
              <div>
                <dt className="field__label">Subject</dt>
                <dd className="approval__field-value" data-testid="lead-approve-review-subject">{draft.subject}</dd>
              </div>
              <div>
                <dt className="field__label">Message</dt>
                <dd className="approval__field-value approval__message" data-testid="lead-approve-review-body">
                  {draft.body}
                </dd>
              </div>
            </dl>
            <div className="chip-row" aria-label="Disclosure and evidence">
              <Chip variant="success" icon="shield">
                Disclosure {draft.disclosure_version} · {draft.disclosure_state || 'state fallback'}
              </Chip>
              {draft.evidence_assets.map((asset, chipIndex) => {
                const source = descriptorFor(asset);
                return (
                  <EvidenceChip
                    key={asset}
                    source={source}
                    onClick={onInspectEvidence ? () => onInspectEvidence(source, chipIndex) : undefined}
                  >
                    {source.title}
                  </EvidenceChip>
                );
              })}
            </div>
          </div>
        )}
        {error && phase === 'ready' && (
          <div className="status-callout status-callout--danger" role="alert">{error}</div>
        )}
      </div>
      <div className="approval__actions">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          aria-disabled={phase === 'submitting' || undefined}
        >
          Cancel
        </Button>
        <Button
          ref={buttonRef}
          type="submit"
          variant="primary"
          size="sm"
          icon={phase === 'submitting' ? undefined : 'check'}
          // aria-disabled, never native `disabled`: a focused Confirm that
          // turned disabled would drop keyboard focus to <body> mid-approval.
          aria-disabled={phase !== 'ready' || undefined}
          aria-keyshortcuts="Enter"
          data-testid="lead-approve-review-confirm"
        >
          {phase === 'submitting' ? 'Approving…' : 'Confirm approval'}
        </Button>
      </div>
    </form>
  );
}

/**
 * The review in the expanded row. Escape cancels it through the shared layer
 * stack, but only while focus is inside the review: the layer declines
 * otherwise, so Escape in a filter or the Genie panel never abandons it.
 */
export function LeadApproveReviewInline(props: LeadApproveReviewProps) {
  const { review, onCancel } = props;
  const wrapperRef = useRef<HTMLDivElement>(null);
  // One layer per open review (pushed on open, popped on close); it calls
  // the latest onCancel, so re-renders never re-order the layer stack.
  const onCancelRef = useRef(onCancel);
  useEffect(() => {
    onCancelRef.current = onCancel;
  }, [onCancel]);
  useEffect(() => pushEscapeLayer(() => {
    if (!wrapperRef.current?.contains(document.activeElement)) return false;
    onCancelRef.current();
    return true;
  }), [review.borrowerId]);
  return (
    <div ref={wrapperRef} className="tbl__expand-inner tbl__expand-inner--review">
      <LeadApproveReview {...props} />
    </div>
  );
}

/**
 * The review as a native modal `<dialog>` when the row is collapsed (or
 * scrolled out of the virtualized window): `showModal()` puts it in the top
 * layer with the page inert behind it, and `useFocusTrap` owns Tab and puts
 * Escape on the shared layer stack (so Escape cancels the review alone).
 */
export function LeadApproveReviewDialog(props: LeadApproveReviewProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { onCancel } = props;
  // Declared BEFORE showModal's effect: the trap records the element to give
  // focus back to on close (the table), and showModal would already have
  // moved focus into the dialog. Initial focus on Confirm (aria-disabled,
  // still focusable, until the draft lands), so the trap never pulls focus
  // back off it once the draft is ready.
  useFocusTrap({ open: true, containerRef: dialogRef, initialFocusRef: props.confirmRef, onClose: () => { onCancel(); } });
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || dialog.open) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }, []);
  return (
    <dialog
      ref={dialogRef}
      className="lead-approve-dialog"
      aria-label={`Review the outreach for ${props.review.borrowerId}`}
      tabIndex={-1}
      onCancel={(event) => {
        // Native Escape: the layer stack already handled it; never let the
        // browser close the dialog behind React's back.
        event.preventDefault();
      }}
    >
      <LeadApproveReview {...props} />
    </dialog>
  );
}
