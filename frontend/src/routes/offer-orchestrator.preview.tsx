import { useApp } from '../components/AppContext';
import { Chip } from '../components/Primitives';
import type { OutreachChannel } from './offer-orchestrator.constants';
import { describeSmsSegments, smsSegments } from './offer-orchestrator.sms-segments';
import './offer-orchestrator.preview.css';

/**
 * CertifiedCopyPreview — the borrower-facing copy on the one screen where a
 * human certifies it, shown the way the borrower would receive it (2026-09-21
 * audit critic-02).
 *
 * Email and direct mail render as a message frame: an `<article>` whose `<dl>`
 * header names who it is From (the tenant lender), who it is To (the masked
 * borrower id, labelled a synthetic contact: Module 0 holds no real contact
 * fields) and the Subject, which wraps; then the body with its paragraphs;
 * then the disclosure the draft was generated under. SMS renders as a message
 * bubble with its carrier segment count (GSM-7 160 / 153, UCS-2 70 / 67), so an
 * approver sees that one curly quote can triple what a send costs.
 *
 * It is read-only, never a form control: wave 0 replaced the clipping
 * `<input readOnly>` and the disabled `<textarea>`, and there is no edit mode.
 * To change the copy the approver regenerates a governed draft. The body is
 * split into paragraphs on blank lines ("\n\n") only, and each paragraph keeps
 * its own whitespace, so the paragraphs joined by "\n\n" are exactly the
 * audited body: nothing is trimmed, collapsed or reflowed.
 *
 * Contract kept from wave 0: `role="group"` + the "… — review only" names (the
 * live e2e locates the draft by that label), the `outreach-subject` /
 * `outreach-draft` testids, and `aria-disabled` while the copy shown is not
 * the copy that would be approved (the draft is loading, failed, or saving).
 * Approval itself is gated on the draft proof elsewhere, not on this flag.
 */
export interface CertifiedCopyPreviewProps {
  channel: OutreachChannel;
  subject: string;
  body: string;
  /** False while the audited draft is loading, failed, or being saved. */
  current: boolean;
  borrowerId: string | null;
  disclosureVersion: string | null;
  disclosureState: string | null;
}

const CHANNEL_NAME: Record<OutreachChannel, string> = {
  email: 'email',
  sms: 'SMS',
  direct_mail: 'letter',
};

/** The audited body as paragraphs; `paragraphs(body).join('\n\n') === body`. */
export function copyParagraphs(body: string): string[] {
  return body.split('\n\n');
}

function CopyParagraphs({ body }: { body: string }) {
  return (
    <>
      {copyParagraphs(body).map((paragraph, index) => (
        // Position is the identity: the copy is replaced whole, never reordered.
        <p key={index} className="certified-copy__para">{paragraph}</p>
      ))}
    </>
  );
}

export function CertifiedCopyPreview({
  channel,
  subject,
  body,
  current,
  borrowerId,
  disclosureVersion,
  disclosureState,
}: CertifiedCopyPreviewProps) {
  const { lender } = useApp();
  const from = lender?.trim() || 'Configured lender';
  const disabled = current ? undefined : true;
  const sms = channel === 'sms';
  const copy = (
    <div
      role="group"
      aria-label="Outreach draft — review only"
      aria-disabled={disabled}
      data-testid="outreach-draft"
      className={sms ? 'certified-copy__bubble' : 'certified-copy__body'}
    >
      <CopyParagraphs body={body} />
    </div>
  );
  return (
    <article
      className={`certified-copy certified-copy--${sms ? 'sms' : 'message'}`}
      aria-label={`Certified ${CHANNEL_NAME[channel]} preview`}
      data-testid="certified-copy"
      data-channel={channel}
    >
      <dl className="certified-copy__meta">
        <div className="certified-copy__field">
          <dt className="certified-copy__term">From</dt>
          <dd className="certified-copy__value">{from}</dd>
        </div>
        <div className="certified-copy__field">
          <dt className="certified-copy__term">To</dt>
          <dd className="certified-copy__value">
            <span className="mono">{borrowerId ?? 'Borrower'}</span>
            <Chip variant="neutral">Synthetic contact</Chip>
          </dd>
        </div>
        {!sms && (
          <div className="certified-copy__field">
            <dt className="certified-copy__term">Subject</dt>
            <dd className="certified-copy__value">
              <div
                role="group"
                aria-label="Outreach subject — review only"
                aria-disabled={disabled}
                data-testid="outreach-subject"
                className="certified-copy__subject"
              >
                {subject}
              </div>
            </dd>
          </div>
        )}
      </dl>
      {sms ? (
        <div className="certified-copy__thread">
          {copy}
          {body.length > 0 && (
            <p className="certified-copy__segments" data-testid="sms-segments">
              {describeSmsSegments(smsSegments(body))}
            </p>
          )}
        </div>
      ) : copy}
      {disclosureVersion && (
        <footer className="certified-copy__footer">
          <Chip variant="success" icon="shield">
            Disclosure {disclosureVersion} · {disclosureState ?? 'state fallback'}
          </Chip>
        </footer>
      )}
    </article>
  );
}
