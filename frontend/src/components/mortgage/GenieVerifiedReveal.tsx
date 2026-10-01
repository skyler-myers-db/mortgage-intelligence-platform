/**
 * "Partial research" (audit 2026-09-21 `genie-01` phase 1b): the deep
 * sweep's sections that already passed their own checks, shown inside the
 * progress card while the summary, the final checks and the recorded answer
 * are still coming. deviation:genie-partial-research-reveal (the prototype's
 * pending state is the typing-dots bubble only).
 *
 * Lazy-loaded by GenieProgress (which sits in the idle-preloaded launcher
 * chunk), so the answer stack below never rides along with the rail. Below
 * the three-section floor there is one count line and no content; from it,
 * the sections render through the answer's own section renderer in preview
 * mode: no row actions, no CSV, no cell or chart links, and a note when the
 * rows were trimmed. Never announced and never inside a live region.
 */
import { genieRevealCountLine, type GenieVerifiedRevealState } from '../../lib/genieVerifiedReveal';
import { GenieAnswerSections } from './GenieAnswer.sections';
import './GenieVerifiedReveal.css';

export default function GenieVerifiedReveal({
  reveal,
  dense = false,
}: {
  reveal: GenieVerifiedRevealState;
  dense?: boolean;
}) {
  if (!reveal.sections || reveal.sections.length === 0) {
    const line = genieRevealCountLine(reveal);
    return line ? <p className="genie-reveal__count">{line}</p> : null;
  }
  return (
    <div className={`genie-reveal${dense ? ' genie-reveal--dense' : ''}`} role="group" aria-label="Partial research">
      <div className="eyebrow genie-reveal__eyebrow">Partial research</div>
      <p className="genie-reveal__note">Verified so far. The summary, the final checks and the recorded answer follow.</p>
      <GenieAnswerSections summary={null} sections={[...reveal.sections]} dense={dense} exportBase={null} preview />
    </div>
  );
}
