import { useEffect } from 'react';
import { actorScopeStatus, subscribeActorScope, takeActorResetNotice } from '../../lib/actorScope';
import { toast } from '../../lib/toast';

export const ACTOR_RESET_NOTICE =
  'The signed-in user changed, so this tab was reset. Nothing from the previous session carries over.';

/**
 * The one-time notice after a proven actor change reset this tab
 * (D-identity-review-a3; lib/actorScope leaves the flag in this tab's
 * sessionStorage before it replaces the document).
 *
 * Shown the first time the gate is open (at mount when it already is, else
 * on the first 'opened' event), as a persistent info toast. A hidden tab
 * shows it on its first change to visible, so nobody misses it. The flag is
 * taken (read and removed) only at the moment the toast is raised, so a
 * StrictMode re-run of this effect raises it once. A storage area that
 * throws lost the flag with the old document: then nothing is shown.
 */
export function useActorResetNotice(): void {
  useEffect(() => {
    const cleanups: Array<() => void> = [];
    const raise = () => {
      if (takeActorResetNotice()) toast.info(ACTOR_RESET_NOTICE);
    };
    const deliver = () => {
      if (document.visibilityState !== 'hidden') {
        raise();
        return;
      }
      const onVisible = () => {
        if (document.visibilityState === 'hidden') return;
        document.removeEventListener('visibilitychange', onVisible);
        raise();
      };
      document.addEventListener('visibilitychange', onVisible);
      cleanups.push(() => document.removeEventListener('visibilitychange', onVisible));
    };
    if (actorScopeStatus() === 'open') {
      deliver();
    } else {
      let waiting = true;
      const unsubscribe = subscribeActorScope(({ reason }) => {
        if (!waiting || reason !== 'opened') return;
        waiting = false;
        unsubscribe();
        deliver();
      });
      cleanups.push(unsubscribe);
    }
    return () => {
      for (const cleanup of cleanups) cleanup();
    };
  }, []);
}
