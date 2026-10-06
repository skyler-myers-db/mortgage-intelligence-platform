import { useEffect, useRef } from 'react';
import { pushEscapeLayer } from '../../lib/escapeStack';

/**
 * GenieRefusalReportConfirm — the consented-capture step of "This was
 * legitimate" (D-audit-reads-d, audit 2026-09-21 `genie-05`). Loaded lazily
 * by <GenieRefusalCard> only when capture is on and the card holds the
 * question, so the card itself grows by the loader alone.
 *
 * It shows the question read-only, says exactly what keeping it means, and
 * offers the two reports and Cancel. Focus moves to the first button on
 * reveal; Esc (on the shared escape stack, so it never also closes the
 * floating Genie panel) or Cancel closes the step and the card returns focus
 * to "This was legitimate". The question is rendered, never stored: it lives
 * only in the conversation the card already holds.
 *
 * `.genie-answer__refusal-confirm` / `-question` / `-disclosure` /
 * `-confirm-actions` are documented extensions of the `.genie-answer` block
 * (design_files/index.html:704-760 defines the Genie panel and its AI answer
 * bubble and has no refusal state); tokens only, CSS in GenieRefusalCard.css.
 * deviation:genie-refusal-confirm
 */

export const REFUSAL_CAPTURE_DISCLOSURE =
  'Your question and your name are kept for 90 days so the governance team can review this refusal. ' +
  'Only administrators and auditors can read it, and every read is logged. Phone numbers, emails, SSNs ' +
  'and street addresses are masked; questions that name a person or a borrower are not kept.';

export interface GenieRefusalReportConfirmProps {
  /** The panel id the card's "This was legitimate" names in aria-controls. */
  id: string;
  question: string;
  onReport: (withQuestion: boolean) => void;
  onCancel: () => void;
}

export function GenieRefusalReportConfirm({ id, question, onReport, onCancel }: GenieRefusalReportConfirmProps) {
  const groupRef = useRef<HTMLDivElement>(null);
  const firstRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  });

  useEffect(() => {
    firstRef.current?.focus();
    // Non-modal: the step takes Esc only while focus is inside it.
    return pushEscapeLayer(() => {
      if (!groupRef.current?.contains(document.activeElement)) return false;
      cancelRef.current();
      return true;
    });
  }, []);

  return (
    <div
      ref={groupRef}
      id={id}
      className="genie-answer__refusal-confirm"
      role="group"
      aria-label="Report this refusal"
      data-testid="genie-refusal-confirm"
    >
      <blockquote className="genie-answer__refusal-question" data-testid="genie-refusal-question">
        {question}
      </blockquote>
      <p className="genie-answer__refusal-disclosure">{REFUSAL_CAPTURE_DISCLOSURE}</p>
      <div className="genie-answer__refusal-confirm-actions">
        <button
          ref={firstRef}
          type="button"
          className="btn btn--primary btn--sm"
          onClick={() => onReport(true)}
          data-testid="genie-refusal-report-with"
        >
          Report with my question
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => onReport(false)}
          data-testid="genie-refusal-report-without"
        >
          Report without it
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel} data-testid="genie-refusal-cancel">
          Cancel
        </button>
      </div>
    </div>
  );
}
