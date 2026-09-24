import { useState } from 'react';
import { genieTurnOutcome } from '../../lib/genieTurnOutcome';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

/** A thread this long starts collapsing the turns the reader never saw land. */
export const GENIE_COLLAPSE_MIN_TURNS = 3;

/**
 * full       render the whole answer, as always;
 * collapsed  an earlier turn shown as its digest, the answer not mounted;
 * expanded   an earlier turn the reader opened again.
 */
export type GenieTurnPresentation = 'full' | 'collapsed' | 'expanded';

/**
 * Which earlier Genie turns render collapsed on this surface (audit
 * 2026-09-21 `genie-08`). A long thread used to render every stored answer
 * in full, so presenters were told to clear the thread for speed.
 *
 * With GENIE_COLLAPSE_MIN_TURNS or more settled turns, a turn collapses to
 * its digest when it is not the latest AND this surface never rendered it in
 * full (it was never the latest while mounted, nor shown while the thread was
 * shorter than the threshold): a turn restored from History or a reload
 * collapses, but one that became "earlier" because a new answer landed under
 * it stays as the reader saw it (no layout shift above the reader). Only
 * answered turns collapse; a withheld or failed turn keeps its explanation in
 * view.
 *
 * Everything is keyed by the turn's response OBJECT, never by its index: the
 * 20-turn store cap evicts from the head and shifts every index, while the
 * store keeps each response object's identity (appendGenieTurn), and a
 * History restore or a reload creates new objects. The memory is component
 * state (the React Compiler forbids reading refs during render), holds only
 * object references, and is never written to any store.
 */
export function useGenieTurnCollapse(responses: readonly GenieAnswerShape[]): {
  presentation: (response: GenieAnswerShape) => GenieTurnPresentation;
  toggle: (response: GenieAnswerShape) => void;
} {
  const latest = responses.length > 0 ? responses[responses.length - 1] : null;
  const short = responses.length < GENIE_COLLAPSE_MIN_TURNS;
  // Every response this surface has rendered in full: the latest of each
  // render, and every turn of a thread still under the threshold. Once shown
  // in full, a turn never collapses under the reader.
  const [shownFull, setShownFull] = useState<readonly GenieAnswerShape[]>(() =>
    responses.filter((response) => short || response === latest),
  );
  // Earlier turns the reader opened again.
  const [opened, setOpened] = useState<readonly GenieAnswerShape[]>([]);
  const newlyFull = responses.filter((response) => (short || response === latest) && !shownFull.includes(response));
  if (newlyFull.length > 0) {
    setShownFull([...shownFull.filter((response) => responses.includes(response)), ...newlyFull]);
  }
  const presentation = (response: GenieAnswerShape): GenieTurnPresentation => {
    if (
      short
      || response === latest
      || shownFull.includes(response)
      || genieTurnOutcome(response) !== 'answered'
    ) {
      return 'full';
    }
    return opened.includes(response) ? 'expanded' : 'collapsed';
  };
  const toggle = (response: GenieAnswerShape) =>
    setOpened((current) =>
      current.includes(response) ? current.filter((item) => item !== response) : [...current, response],
    );
  return { presentation, toggle };
}
