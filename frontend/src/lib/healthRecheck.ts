/**
 * A request for one more trusted health probe (D-identity-review-a3, audit
 * genie-02 / bundle-04 item 4).
 *
 * lib/actorScope SUSPENDS a tab whose shared local owner stamp another tab
 * changed: the gate closes at once, but only a trusted probe may say who the
 * actor now is. The gate asks here; components/healthPoll.ts listens and
 * probes as soon as it may (a tab that is hidden, offline or session-expired
 * probes later, on its own visibility, online or reload path, and that probe
 * answers the request). The first trusted probe STARTED after the request
 * publishes a fresh identity object even for an unchanged key, so a probe
 * that answers the same actor still reaches the gate and reopens it.
 *
 * A plain module set, like lib/apiFailure's network-failure nudges (owned by
 * another module), so the gate never imports the poll or React.
 */

type RecheckListener = () => void;

const listeners = new Set<RecheckListener>();

/** Ask the shell's health poll for one more trusted observation. */
export function requestHealthRecheck(): void {
  for (const listener of [...listeners]) {
    try {
      listener();
    } catch (err) {
      queueMicrotask(() => {
        throw err;
      });
    }
  }
}

/** The health poll's subscription; returns the unsubscribe. */
export function subscribeHealthRecheck(listener: RecheckListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
