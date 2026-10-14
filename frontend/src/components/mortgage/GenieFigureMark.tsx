/**
 * One figure in Genie's answer prose that matches a returned cell (audit
 * 2026-09-21 `genie-10` phase 2). deviation:genie-figure-highlight.
 *
 * Lazy (GenieAnswerFigures loads it with React.lazy when an answer has a
 * figure to mark), with its stylesheet: until it arrives the token renders as
 * plain text, so the ask-genie route closure carries only the decision code.
 *
 * The mark is a PASSIVE preview: the existing evidence hover card on pointer
 * hover only. It is not focusable and has no title; keyboard and
 * screen-reader users read the same fact in the proof drawer's Verified
 * figures list. A hover preloads the evidence drawer's body chunk only, never
 * data.
 */
import type { DrawerSource } from '../AppContext';
import { useEvidenceHoverCard } from '../EvidenceHoverCard';
import { humanizeKey } from './GenieAnswer.logic';
import './GenieAnswerFigures.css';

export const FIGURE_HOVER_TITLE = 'Matches a returned value';
export const FIGURE_HOVER_CTA = 'Listed under Verified figures in the proof';

const HOVER_OPTIONS = { cta: FIGURE_HOVER_CTA };

/** The hover card's source: the fact, and the returned column when named. */
function figureSource(column: string | null): DrawerSource {
  return {
    title: FIGURE_HOVER_TITLE,
    signals: column ? [{ label: 'Column', source: 'Returned rows', value: humanizeKey(column) }] : undefined,
  };
}

export default function GenieFigureMark({ token, column }: { token: string; column: string | null }) {
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(figureSource(column), HOVER_OPTIONS);
  const { onMouseEnter, onMouseLeave } = anchorHandlers;
  // Pointer only, attached as native mouseenter/mouseleave listeners: the
  // mark stays a non-interactive element (no focus, no key handlers, no
  // role), because the same fact is in the proof drawer's Verified figures
  // list for keyboard and screen-reader users. Do not move these to JSX
  // onMouseEnter/onMouseLeave props: jsx-a11y no-noninteractive-element-
  // interactions then asks for an interactive role or element, and making the
  // mark interactive is the opposite of the declared design
  // (docs/prototype-deviations.md genie-figure-highlight).
  const markRef = (el: HTMLElement | null) => {
    anchorRef(el);
    if (!el) return undefined;
    el.addEventListener('mouseenter', onMouseEnter);
    el.addEventListener('mouseleave', onMouseLeave);
    return () => {
      el.removeEventListener('mouseenter', onMouseEnter);
      el.removeEventListener('mouseleave', onMouseLeave);
      anchorRef(null);
    };
  };
  return (
    <>
      <mark ref={markRef} className="genie-md-figure">
        {token}
      </mark>
      {hoverCard}
    </>
  );
}
