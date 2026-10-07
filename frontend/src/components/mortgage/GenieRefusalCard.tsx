import { useEffect, useId, useRef, useState } from 'react';
import { Link, useInRouterContext } from 'react-router';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { ApiError } from '../../lib/api';
import { genieRefusalReportApi, type GenieRefusalReportRequest } from '../../lib/apiClients/genieRefusalReport';
import { glossaryAnchor } from '../../lib/mortgageGlossary';
import { useRefusalTextCapture } from '../../lib/optionalQueryReads';
import { Icon } from '../Icon';
import { lazyModule, useLazyModule } from './useLazyModule';
import {
  GENIE_REFUSAL_FAMILIES,
  refusalFamilyFor,
  refusalRephraseChips,
} from './genieRefusal';
import './GenieRefusalCard.css';

/**
 * GenieRefusalCard — the helpful, compliant refusal (audit 2026-09-21
 * `genie-05`). Rendered by <GenieAnswer> under the refusal sentence of a
 * withheld turn (`source` `refused` / `policy_blocked`):
 *
 *   - one plain-language sentence per coarse family: what Genie answers;
 *   - 2-3 rephrase chips, pre-validated through the real guard battery
 *     (tests/unit/test_genie_refusal_chips.py), asked as a new question;
 *   - "Edit question", which puts the ORIGINAL prompt back in the composer
 *     (client-side only: the panel still holds it until the user leaves);
 *   - the reviewed vocabulary in the glossary;
 *   - "This was legitimate", a false-positive report latched so a
 *     double-click cannot file twice. One click files it hash-only (the
 *     family and the `refusal_report_hash` the turn returned). When the
 *     tenant's capture switch is on, the card holds the question and the
 *     family is not pii_request, it instead reveals the consented step
 *     (GenieRefusalReportConfirm, D-audit-reads-d), whose "Report with my
 *     question" adds the question exactly as it was asked. The confirm
 *     loads through useLazyModule, not lazyWithPreload, because it needs
 *     props (deviation:genie-refusal-confirm); a chunk that fails to load
 *     shows a line and never files.
 *
 * The copy explains the product's scope; it never says the guard was wrong.
 * `.genie-answer__refusal*` is a documented BEM extension of the
 * prototype's `.genie-answer` block (design_files/index.html has no
 * refusal state); the chips reuse `.filter--question` and the buttons
 * `.btn--ghost`, tokens only.
 */

interface GenieRefusalCardProps {
  payload: GenieAnswerShape;
  /** The question that was refused (lives in the conversation, not the payload). */
  question?: string;
  onFollowUp?: (question: string) => void;
  onEditQuestion?: (question: string) => void;
  followUpDisabledReason?: string | null;
}

const GLOSSARY_HREF = glossaryAnchor('reviewedVocabulary');

type ReportOutcome = { ok: true; captured: boolean } | { ok: false; message: string };

const REPORT_CONFIRM = lazyModule(() => import('./GenieRefusalReportConfirm'));
const CONFIRM_UNAVAILABLE = 'The report options could not load. Please try again.';

/**
 * File the report. Never rejects: a failure becomes its fixed line. Outside
 * the component so the report handler needs no try statement (audit
 * 2026-09-21 `runtime-03`: a try/finally stops the React Compiler).
 */
function fileRefusalReport(body: GenieRefusalReportRequest): Promise<ReportOutcome> {
  return genieRefusalReportApi.report(body).then(
    (result): ReportOutcome => ({ ok: true, captured: result.question_captured === true }),
    (err: unknown): ReportOutcome => ({
      ok: false,
      message:
        err instanceof ApiError && err.status === 422
          ? 'This refusal could not be reported from this answer.'
          : 'The report could not be recorded. Please try again.',
    }),
  );
}

function GlossaryLink() {
  const inRouter = useInRouterContext();
  const body = (
    <>
      <Icon name="shield" size={12} />
      Reviewed vocabulary
    </>
  );
  return inRouter ? (
    <Link className="btn btn--ghost btn--sm" to={GLOSSARY_HREF}>
      {body}
    </Link>
  ) : (
    <a className="btn btn--ghost btn--sm" href={GLOSSARY_HREF}>
      {body}
    </a>
  );
}

export function GenieRefusalCard({
  payload,
  question,
  onFollowUp,
  onEditQuestion,
  followUpDisabledReason = null,
}: GenieRefusalCardProps) {
  const reason = refusalFamilyFor(payload);
  const family = GENIE_REFUSAL_FAMILIES[reason];
  const chips = refusalRephraseChips(reason);
  const reportHash = payload.refusal_report_hash ?? null;
  const [reported, setReported] = useState<string | null>(null);
  const [reporting, setReporting] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  // The consented step is offered only when the server would keep the text:
  // capture on, the question held, and not a refused request for PII.
  const offerQuestion = useRefusalTextCapture() && Boolean(question) && reason !== 'pii_request';
  const [confirmOpen, setConfirmOpen] = useState(false);
  const confirmChunk = useLazyModule(REPORT_CONFIRM, confirmOpen);
  const Confirm = confirmChunk.module?.GenieRefusalReportConfirm ?? null;
  const confirmId = useId();
  const reportButtonRef = useRef<HTMLButtonElement>(null);
  // Async latch: a second click before React re-renders the disabled state
  // must not file a second report (mirrors GenieAnswerFeedback).
  const inFlightRef = useRef(false);
  const identity = `${reportHash ?? ''}:${reason}`;
  const identityRef = useRef(identity);
  // The report button unmounts on success; focus moves to its confirmation
  // so keyboard and screen-reader users keep their place (not <body>).
  const reportedRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    identityRef.current = identity;
    inFlightRef.current = false;
    setReported(null);
    setReporting(false);
    setReportError(null);
    setConfirmOpen(false);
  }, [identity]);

  useEffect(() => {
    if (reported) reportedRef.current?.focus();
  }, [reported]);

  const report = (withQuestion: boolean) => {
    if (!reportHash || inFlightRef.current || reported) return;
    inFlightRef.current = true;
    setReporting(true);
    setReportError(null);
    const submitted = identity;
    const body: GenieRefusalReportRequest = {
      question_hash: reportHash,
      refusal_reason: reason,
      conversation_id: payload.conversation_id ?? null,
      message_id: payload.message_id ?? null,
    };
    if (withQuestion && question) body.question_text = question;
    void fileRefusalReport(body).then((outcome) => {
      // A report for a card that now shows another refusal changes nothing.
      if (identityRef.current !== submitted) return;
      if (outcome.ok) {
        setReported(withQuestion && !outcome.captured ? 'Reported without your question' : 'Reported for review');
        setConfirmOpen(false);
      } else setReportError(outcome.message);
      inFlightRef.current = false;
      setReporting(false);
    });
  };

  const closeConfirm = () => {
    setConfirmOpen(false);
    reportButtonRef.current?.focus();
  };

  return (
    <div
      className="genie-answer__refusal"
      role="group"
      // The group holds the rewordings AND Edit question, the glossary link
      // and the report control, so it is named for all of them.
      aria-label="Refusal options"
      data-testid="genie-refusal-card"
      data-refusal-reason={reason}
    >
      <div className="genie-answer__refusal-hdr">
        <Icon name="info" size={12} className="icon-accent" />
        <span className="genie-answer__refusal-title">{family.title}</span>
      </div>
      <p className="genie-answer__refusal-sentence">{family.sentence}</p>
      {onFollowUp && chips.length > 0 && (
        <div
          className="genie-answer__refusal-chips"
          role="group"
          aria-label="Ask this instead"
        >
          {chips.map((chip) => (
            <button
              key={chip}
              type="button"
              className="filter filter--question"
              onClick={() => onFollowUp(chip)}
              disabled={Boolean(followUpDisabledReason)}
              title={followUpDisabledReason ?? undefined}
              data-testid="genie-refusal-chip"
            >
              <span className="filter__label">Ask</span>
              <span className="filter__value filter__value--question">{chip}</span>
            </button>
          ))}
        </div>
      )}
      <div className="genie-answer__refusal-actions">
        {onEditQuestion && question && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onEditQuestion(question)}
            data-testid="genie-refusal-edit"
          >
            <Icon name="tweak" size={12} />
            Edit question
          </button>
        )}
        <GlossaryLink />
        {reportHash && !reported && (
          <button
            ref={reportButtonRef}
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => (offerQuestion ? setConfirmOpen((open) => !open) : report(false))}
            disabled={reporting}
            aria-expanded={offerQuestion ? confirmOpen : undefined}
            aria-controls={offerQuestion && confirmOpen ? confirmId : undefined}
            // WCAG 2.5.3: the accessible name starts with the visible label.
            aria-label="This was legitimate: report this refusal for review"
            data-testid="genie-refusal-report"
          >
            <Icon name="thumbup" size={12} />
            This was legitimate
          </button>
        )}
        {reported && (
          <span
            ref={reportedRef}
            className="genie-answer__refusal-reported"
            role="status"
            tabIndex={-1}
          >
            <Icon name="check" size={12} className="icon-accent" />
            {reported}
          </span>
        )}
      </div>
      {confirmOpen && !reported && Confirm && question && (
        <Confirm id={confirmId} question={question} onReport={report} onCancel={closeConfirm} />
      )}
      {confirmOpen && confirmChunk.failed && (
        <p className="genie-answer__refusal-error" role="alert">
          {CONFIRM_UNAVAILABLE}
        </p>
      )}
      {reportError && (
        <p className="genie-answer__refusal-error" role="alert">
          {reportError}
        </p>
      )}
    </div>
  );
}
