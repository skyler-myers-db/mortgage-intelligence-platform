import type { GenieAnswer as GenieAnswerShape } from '../types';
import { NON_PERSISTABLE_SOURCES } from './pinnedInsights';

/**
 * What a settled Genie turn means for the user, and the words the surfaces
 * use for it (audit 2026-09-21 `a11y-06`, `runtime-01`). Split out of the
 * in-flight turn store (lib/genieInFlightTurn.ts) so the store stays about
 * the lifecycle; the store re-exports everything here.
 */

/** Source of a governed action's result bubble (never a Genie answer). */
export const GOVERNED_ACTION_SOURCE = 'governed_action';

/** answered: a renderable governed answer. withheld: refused, policy
 *  blocked, a data gap or out of footprint. failed: degraded or an error. */
export type GenieTurnOutcome = 'answered' | 'withheld' | 'failed';

/** Said once, and only when the governed answer is in hand and renderable. */
export const GENIE_ANSWER_READY = 'Answer ready';
export const GENIE_WITHHELD_ANNOUNCEMENT = 'Genie did not answer this question. The reason is shown in the thread.';
export const GENIE_FAILED_ANNOUNCEMENT = 'Genie could not complete this question.';

/** The one busy reason both surfaces show while a turn from EITHER is in
 *  flight (audit `genie-v2`): Ask, the chips, Regenerate / Retry, History and
 *  New thread are held until it lands or the user presses Stop. */
export const GENIE_BUSY_REASON =
  'Genie is still answering. Ask unlocks when this answer lands, or press Stop. Leaving this page does not stop it.';

/** The Stopped note. No server cancel exists: the copy never claims one. */
export const GENIE_STOPPED_REASON =
  'Stopped before the answer arrived. Genie may still finish this turn on the server; that reply is ' +
  'discarded and never shown, but Genie may keep the question as context for the next turn in this thread.';

/** A resumed turn that failed before its question could be shown again. */
export const GENIE_RESUME_FAILED_REASON = 'Could not resume your last question after the reload. Ask it again.';

export type GenieInterruption = 'reload' | 'completing' | 'other-tab';

/** Why a turn a reload found could not resume. */
export function interruptedReason(kind: GenieInterruption): string {
  if (kind === 'completing') {
    // The server finishes a complete it already received: the answer may be
    // in the thread's history even though this tab never saw it.
    return 'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.';
  }
  if (kind === 'other-tab') return 'This question is being answered in another tab.';
  return 'Interrupted by a reload before the answer arrived. Ask again to get it.';
}

/** A trusted answer continues its Genie conversation; a caveat never does. */
export function shouldPersistConversation(payload: GenieAnswerShape): boolean {
  return Boolean(payload.conversation_id && !NON_PERSISTABLE_SOURCES.has(String(payload.source ?? '')));
}

export function genieTurnOutcome(payload: GenieAnswerShape): GenieTurnOutcome {
  const source = String(payload.source ?? '');
  if (source === 'degraded') return 'failed';
  if (NON_PERSISTABLE_SOURCES.has(source) || source === GOVERNED_ACTION_SOURCE) return 'withheld';
  return 'answered';
}

export function genieOutcomeAnnouncement(outcome: GenieTurnOutcome): string {
  if (outcome === 'answered') return GENIE_ANSWER_READY;
  if (outcome === 'withheld') return GENIE_WITHHELD_ANNOUNCEMENT;
  return GENIE_FAILED_ANNOUNCEMENT;
}
