import { useEffect, useRef, useState } from 'react';
import { Link, useInRouterContext } from 'react-router';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { ApiError, api } from '../../lib/api';
import { glossaryAnchor } from '../../lib/mortgageGlossary';
import { Icon } from '../Icon';
import {
  GENIE_REFUSAL_FAMILIES,
  refusalFamilyFor,
  refusalRephraseChips,
} from './genieRefusal';

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
 *   - "This was legitimate", a hash-only false-positive report. The POST
 *     carries the family and the `refusal_report_hash` the turn returned,
 *     never the question text, and is latched so a double-click cannot
 *     file twice.
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
  const [reported, setReported] = useState(false);
  const [reporting, setReporting] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  // Async latch: a second click before React re-renders the disabled state
  // must not file a second report (mirrors GenieAnswerFeedback).
  const inFlightRef = useRef(false);
  const identity = `${reportHash ?? ''}:${reason}`;
  const identityRef = useRef(identity);

  useEffect(() => {
    identityRef.current = identity;
    inFlightRef.current = false;
    setReported(false);
    setReporting(false);
    setReportError(null);
  }, [identity]);

  const report = async () => {
    if (!reportHash || inFlightRef.current || reported) return;
    inFlightRef.current = true;
    setReporting(true);
    setReportError(null);
    const submitted = identity;
    try {
      await api.genieRefusalReport({
        question_hash: reportHash,
        refusal_reason: reason,
        conversation_id: payload.conversation_id ?? null,
        message_id: payload.message_id ?? null,
      });
      if (identityRef.current === submitted) setReported(true);
    } catch (err) {
      if (identityRef.current !== submitted) return;
      setReportError(
        err instanceof ApiError && err.status === 422
          ? 'This refusal could not be reported from this answer.'
          : 'The report could not be recorded. Please try again.',
      );
    } finally {
      if (identityRef.current === submitted) {
        inFlightRef.current = false;
        setReporting(false);
      }
    }
  };

  return (
    <div
      className="genie-answer__refusal"
      role="group"
      aria-label="What Genie can answer instead"
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
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void report()}
            disabled={reporting}
            aria-label="Report this refusal as a legitimate question"
            data-testid="genie-refusal-report"
          >
            <Icon name="thumbup" size={12} />
            This was legitimate
          </button>
        )}
        {reported && (
          <span className="genie-answer__refusal-reported" role="status">
            <Icon name="check" size={12} className="icon-accent" />
            Reported for review
          </span>
        )}
      </div>
      {reportError && (
        <p className="genie-answer__refusal-error" role="alert">
          {reportError}
        </p>
      )}
    </div>
  );
}
