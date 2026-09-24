/**
 * The Web Lock that makes a Genie turn single-owner across tabs (audit
 * 2026-09-21 `runtime-01`, critic fix 13).
 *
 * Browsers copy sessionStorage into a duplicated tab, so the copy would find
 * the in-flight record and resume -- and complete -- a turn the original tab
 * is still running. Every live turn holds `mip-genie-turn:<messageId>` from
 * the moment its ids are known until it settles, stops or resets (the browser
 * releases it on unload), and a resume only proceeds when it can take the
 * lock with `ifAvailable`. Without `navigator.locks` a resume never proceeds
 * (fail closed); fresh turns still run.
 */

export type GenieTurnLockOutcome =
  | { kind: 'held'; release: () => void }
  | { kind: 'busy' }
  | { kind: 'unsupported' };

/** Injected by tests: happy-dom has no `navigator.locks`. Never rejects. */
export type GenieTurnLockRequester = (name: string) => Promise<GenieTurnLockOutcome>;

export function genieTurnLockName(messageId: string): string {
  return `mip-genie-turn:${messageId}`;
}

export function browserGenieTurnLock(name: string): Promise<GenieTurnLockOutcome> {
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks;
  if (!locks || typeof locks.request !== 'function') return Promise.resolve({ kind: 'unsupported' });
  return new Promise<GenieTurnLockOutcome>((resolve) => {
    locks
      .request(name, { ifAvailable: true }, (lock) => {
        if (!lock) {
          resolve({ kind: 'busy' });
          return undefined;
        }
        // The lock is held until this promise settles: release() settles it.
        return new Promise<void>((release) => {
          resolve({ kind: 'held', release: () => release() });
        });
      })
      .catch(() => resolve({ kind: 'unsupported' }));
  });
}
