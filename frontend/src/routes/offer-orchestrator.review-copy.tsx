import type { OutreachChannel } from './offer-orchestrator.constants';
import './offer-orchestrator.review-copy.css';

/**
 * OutreachReviewCopy — the borrower-facing subject and message on the one
 * screen where a human certifies them (2026-09-21 audit critic-02).
 *
 * They used to be form controls that were never editable: the subject a
 * class-less, browser-default `<input readOnly>` that clipped the copy to
 * about 24 characters, the message a disabled `<textarea>` with a resize grip
 * that scrolled past twelve lines. An approver could certify text they had not
 * been shown. Both are now plain read-only text that wraps and grows with the
 * copy, so every character being approved is on screen.
 *
 * `role="group"` + `aria-label` keep the "… — review only" names (the live
 * e2e locates the draft by that label), and the data-testids are unchanged.
 *
 * `current` is false while the audited draft is loading, failed, or being
 * saved. The text shown is then not the copy that would be approved, so it is
 * dimmed and marked aria-disabled — what `disabled` said on the old controls.
 * Approval itself is gated elsewhere, on the draft proof, not on this flag.
 */
interface OutreachReviewCopyProps {
  channel: OutreachChannel;
  subject: string;
  body: string;
  current: boolean;
}

export function OutreachReviewCopy({ channel, subject, body, current }: OutreachReviewCopyProps) {
  const disabled = current ? undefined : true;
  return (
    <>
      {channel !== 'sms' && (
        <div className="mb-3">
          <div className="field__label">Subject</div>
          <div
            role="group"
            aria-label="Outreach subject — review only"
            aria-disabled={disabled}
            data-testid="outreach-subject"
            className="route-textarea route-textarea--readout route-textarea--subject mt-1"
          >
            {subject}
          </div>
        </div>
      )}
      <div
        role="group"
        aria-label="Outreach draft — review only"
        aria-disabled={disabled}
        data-testid="outreach-draft"
        className="route-textarea route-textarea--outreach route-textarea--readout"
      >
        {body}
      </div>
    </>
  );
}
