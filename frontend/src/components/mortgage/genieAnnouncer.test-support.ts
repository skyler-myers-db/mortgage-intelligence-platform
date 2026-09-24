/**
 * Test support for the Genie announcer suites (happy-dom only; never imported
 * by app code).
 *
 * happy-dom holds each MutationObserver callback only through a WeakRef
 * (its MutationObserverListener), so a garbage collection inside a long async
 * window -- a full-suite run under load -- silently drops every later record.
 * A watch that expects an insertion then fails at random, and one that
 * expects NO mutation passes vacuously. Proven with --expose-gc: after two
 * gc() calls a plainly observed node reported nothing while a strongly
 * observed one reported its insertion.
 */

/** A strong stand-in for WeakRef while happy-dom builds the listener. */
class StrongRef<T extends object> {
  readonly #target: T;
  constructor(target: T) {
    this.#target = target;
  }
  deref(): T {
    return this.#target;
  }
}

const EVERY_TEXT_CHANGE: MutationObserverInit = { childList: true, characterData: true, subtree: true };

/** The app's tsconfig targets ES2020, whose lib has no WeakRef, so the global
 *  is reached through this narrow view instead of `globalThis.WeakRef`. */
interface WeakRefHost {
  WeakRef: unknown;
}

/** `observer.observe(target, options)` with the callback held strongly. The
 *  global WeakRef is swapped only for the synchronous observe() call. */
export function observeStrongly(
  observer: MutationObserver,
  target: Node,
  options: MutationObserverInit = EVERY_TEXT_CHANGE,
): void {
  const host = globalThis as unknown as WeakRefHost;
  const weakRef = host.WeakRef;
  host.WeakRef = StrongRef;
  try {
    observer.observe(target, options);
  } finally {
    host.WeakRef = weakRef;
  }
}
