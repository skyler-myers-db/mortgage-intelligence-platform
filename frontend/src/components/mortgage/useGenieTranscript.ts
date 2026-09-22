import { useEffect, useState } from 'react';
import {
  getGenieTurns,
  subscribeGenieTurns,
  turnsToMessages,
  type GenieChatMessage,
  type GenieTurn,
} from '../../lib/genieConversationStore';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

/**
 * Settled transcript of the floating Genie panel, mirrored from the shared
 * conversation store.
 *
 * The panel used to keep a private `msgs` list hydrated once at mount and
 * written back wholesale. That was safe only because the panel unmounted on
 * every close. Now that it stays mounted (audit 2026-09-21 `runtime-01`), a
 * private copy would go stale the moment `/ask-genie` appended a turn, and the
 * next panel write would overwrite the route's turn. So the store is the
 * single source of truth: the panel appends through `appendGenieTurn`, and
 * every change — its own, the route's, a History restore, an actor-boundary
 * clear — arrives here the same way.
 *
 * Plain `useState` fed by the subscription (not `useSyncExternalStore`) on
 * purpose: a landing answer updates the store AND clears the panel's pending
 * question in the same tick, and both must commit together. A sync-external-
 * store update renders in its own lane first, which would paint one frame of
 * the settled question next to its still-pending twin.
 */
export function useGenieTranscript(
  sourcesFor: (payload: GenieAnswerShape) => string[],
): GenieChatMessage[] {
  const [mirror, setMirror] = useState(() => {
    const turns = getGenieTurns();
    return { turns, messages: turnsToMessages(turns, sourcesFor) };
  });

  useEffect(() => {
    const sync = () => {
      const turns: GenieTurn[] = getGenieTurns();
      // The store hands out a stable array between writes, so identity is the
      // change signal; an unchanged list keeps the same state object.
      setMirror((prev) =>
        prev.turns === turns ? prev : { turns, messages: turnsToMessages(turns, sourcesFor) },
      );
    };
    // Catch a write that landed between the first render and this subscribe.
    sync();
    return subscribeGenieTurns(sync);
  }, [sourcesFor]);

  return mirror.messages;
}
