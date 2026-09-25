import { useId, type ReactNode } from 'react';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { answerDigest } from '../../lib/genieAnswerText';
import { normalizeGenieAnswerLanguage } from '../../lib/genieAnswerLanguage';
import { stripQuestionRestatement } from './GenieAnswer.markdown';
import './GenieAnswerReading.css';

/**
 * An earlier Genie turn, collapsed (audit 2026-09-21 `genie-08`). The
 * surface keeps the question bubble and the source/evidence chips exactly as
 * they were; this replaces only the answer: its one-line digest (the metric,
 * else the summary, else the answer, in the words the bubble displays) and a
 * disclosure toggle. The full answer (`children`) is not mounted while
 * collapsed, which is what keeps a long thread fast; the toggle has no
 * aria-controls because the region it would name is not in the DOM then.
 * Every collapsed toggle reads "Show full answer", so while collapsed it is
 * described by its own digest: a screen reader can tell the turns apart.
 *
 * `.genie-collapse` is a documented new block: the prototype
 * (design_files/index.html:741-760) renders every turn in full.
 */
export function GenieCollapsedTurn({
  payload,
  expanded,
  onToggle,
  children,
}: {
  payload: GenieAnswerShape;
  expanded: boolean;
  onToggle: () => void;
  /** The full answer, rendered only while expanded. */
  children: ReactNode;
}) {
  const digestId = useId();
  const cleaned = payload.answer ? normalizeGenieAnswerLanguage(stripQuestionRestatement(payload.answer)) : '';
  return (
    <div className={`genie-collapse${expanded ? ' is-expanded' : ''}`}>
      <button
        type="button"
        className="btn btn--ghost btn--sm genie-collapse__toggle"
        aria-expanded={expanded}
        aria-describedby={expanded ? undefined : digestId}
        onClick={onToggle}
      >
        <Icon name="chevdown" size={12} />
        {expanded ? 'Collapse answer' : 'Show full answer'}
      </button>
      {expanded ? (
        children
      ) : (
        <p id={digestId} className="genie-collapse__digest">
          {answerDigest(payload, cleaned)}
        </p>
      )}
    </div>
  );
}
