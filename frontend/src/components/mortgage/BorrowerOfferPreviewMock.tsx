import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Borrower360 } from '../../types';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { offerDisplayLabel } from '../../lib/offerLanguage';

/**
 * Borrower-facing offer experience — PROTOTYPE (auto-offer program, Module 1).
 *
 * This is the "art of the possible" the Cotality conversation asked for: the
 * borrower sees a compelling pre-qualified offer and accepts in one click
 * instead of waiting for a loan-officer phone call. It is deliberately and
 * unmistakably a MOCK — watermarked, banner-labelled, and wired to NOTHING:
 * no credit pull, no application, no submission, no message sent. It exists so
 * a presenter can show the vision honestly. A real version is the regulated
 * Module 1 program (FCRA firm-offer + RESPA Loan-Estimate timing + a borrower
 * app), explicitly NOT built here. No APR/payment/"trigger terms" are shown,
 * so it makes no advertising/TILA claim.
 */
/**
 * A single, borrower-friendly, QUALITATIVE reason this profile may qualify —
 * grounded in the dossier's strongest public-record signal. Deliberately no
 * figures (no rate/APR/payment/$ → no TILA trigger term) and no protected-class
 * or demographic language (fair-lending). Returns '' when no signal is
 * confidently present, so the mock never fabricates a reason.
 */
function borrowerReason(b: Borrower360): string {
  if (b.is_competitor_lien) return 'Your mortgage is currently with another lender — you may have options.';
  if (typeof b.rate_spread_bps === 'number' && b.rate_spread_bps > 0) {
    return 'Your current rate may be higher than today’s market.';
  }
  const hasEquity =
    (typeof b.ltv === 'number' && b.ltv > 0 && b.ltv < 80) ||
    (typeof b.equity_estimate === 'number' && b.equity_estimate > 0);
  if (hasEquity) return 'Your estimated home equity may open up new options.';
  if (b.is_investor && (b.related_property_count ?? 0) > 1) {
    return 'Your property portfolio may qualify for tailored options.';
  }
  return '';
}

export function BorrowerOfferPreviewMock({
  borrower,
  onClose,
}: {
  borrower: Borrower360;
  onClose: () => void;
}) {
  const { lender } = useApp();
  const [accepted, setAccepted] = useState(false);
  const product = offerDisplayLabel(borrower.recommended_offer_code, borrower.recommended_offer || 'a mortgage option');
  const lenderName = (lender || 'Your lender').trim();
  const reason = borrowerReason(borrower);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  // Modal focus trap + Escape-to-close + focus restoration (mirrors the
  // command palette / drawer pattern).
  useFocusTrap({ open: true, containerRef: panelRef, initialFocusRef: closeRef, onClose });

  if (typeof document === 'undefined') return null;

  return createPortal(
    <div
      className="offer-mock-scrim"
      role="dialog"
      aria-modal="true"
      aria-label="Borrower offer experience — prototype"
      data-testid="borrower-offer-mock"
    >
      <div className="offer-mock" ref={panelRef}>
        <span className="offer-mock__watermark" aria-hidden="true">PROTOTYPE</span>
        <div className="offer-mock__banner" role="note">
          <Icon name="info" size={12} />
          Prototype of the borrower-facing experience (Module 1). Not a live offer, credit
          decision, or application.
        </div>
        <button ref={closeRef} className="offer-mock__close" onClick={onClose} type="button" aria-label="Close prototype">
          <Icon name="close" size={14} />
        </button>

        {!accepted ? (
          <div className="offer-mock__card">
            <div className="offer-mock__brand">{lenderName}</div>
            <h2 className="offer-mock__headline">You may be pre-qualified for {product}.</h2>
            {reason && <p className="offer-mock__reason">{reason}</p>}
            <p className="offer-mock__sub">
              Based on your property and mortgage profile, {lenderName} has an option that may
              fit. Tell us if you&apos;re interested — a licensed loan officer follows up, with no
              obligation.
            </p>
            <div className="offer-mock__cta-row">
              <button
                className="btn btn--primary offer-mock__yes"
                type="button"
                onClick={() => setAccepted(true)}
                data-testid="offer-mock-accept"
              >
                Yes, I&apos;m interested
              </button>
              <button className="btn" type="button" onClick={onClose}>Not now</button>
            </div>
            <p className="offer-mock__legal">
              Illustrative prototype for demonstration only. This is not a firm offer of credit, a
              credit decision, or an application. In production this initiates a compliant,
              disclosed application flow (FCRA firm-offer rules + a Loan Estimate within RESPA
              timing).
            </p>
          </div>
        ) : (
          <div className="offer-mock__card offer-mock__card--done" role="status">
            <div className="offer-mock__check"><Icon name="check" size={28} /></div>
            <h2 className="offer-mock__headline">Thanks — a loan officer will reach out.</h2>
            <p className="offer-mock__sub">
              Prototype only — no information was submitted and no offer was made.
            </p>
            <button className="btn btn--primary" type="button" onClick={onClose}>Close prototype</button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
