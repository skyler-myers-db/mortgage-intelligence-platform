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

/** `observer.observe(target, options)` with the callback held strongly. The
 *  global WeakRef is swapped only for the synchronous observe() call. */
export function observeStrongly(
  observer: MutationObserver,
  target: Node,
  options: MutationObserverInit = EVERY_TEXT_CHANGE,
): void {
  const weakRef = globalThis.WeakRef;
  globalThis.WeakRef = StrongRef as unknown as WeakRefConstructor;
  try {
    observer.observe(target, options);
  } finally {
    globalThis.WeakRef = weakRef;
  }
}
