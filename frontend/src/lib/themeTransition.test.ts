/**
 * @vitest-environment happy-dom
 *
 * runThemeTransition (2026-09-21 audit motion-03 setTheme; stack-04): a
 * View Transition only where the API exists and motion is allowed, the
 * switching attribute held for exactly its length, and every promise it
 * returns swallowed so a skipped transition never becomes a client_error.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { THEME_SWITCHING_ATTRIBUTE, runThemeTransition } from './themeTransition';

declare const process: {
  on(event: 'unhandledRejection', listener: (reason: unknown) => void): void;
  off(event: 'unhandledRejection', listener: (reason: unknown) => void): void;
};

interface Deferred {
  promise: Promise<undefined>;
  resolve: () => void;
  reject: (reason: unknown) => void;
}

function deferred(): Deferred {
  let resolve: () => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<undefined>((res, rej) => {
    resolve = () => res(undefined);
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface StubbedTransition {
  callback: (() => void) | null;
  ready: Deferred;
  updateCallbackDone: Deferred;
  finished: Deferred;
}

/** A document.startViewTransition that runs its callback on a microtask, as browsers do. */
function stubStartViewTransition(): { calls: StubbedTransition[] } {
  const calls: StubbedTransition[] = [];
  Object.defineProperty(document, 'startViewTransition', {
    configurable: true,
    writable: true,
    value: (callback: () => void) => {
      const record: StubbedTransition = {
        callback,
        ready: deferred(),
        updateCallbackDone: deferred(),
        finished: deferred(),
      };
      calls.push(record);
      queueMicrotask(() => {
        try {
          callback();
          record.updateCallbackDone.resolve();
        } catch (error) {
          record.updateCallbackDone.reject(error);
        }
      });
      return {
        ready: record.ready.promise,
        updateCallbackDone: record.updateCallbackDone.promise,
        finished: record.finished.promise,
        skipTransition: () => undefined,
      };
    },
  });
  return { calls };
}

function stubReducedMotion(reduce: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({ media: query, matches: reduce && query.includes('reduce') }),
  });
}

const flushMicrotasks = () => new Promise((resolve) => window.setTimeout(resolve, 0));
const switching = () => document.documentElement.hasAttribute(THEME_SWITCHING_ATTRIBUTE);

describe('runThemeTransition', () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    stubReducedMotion(false);
    document.documentElement.removeAttribute(THEME_SWITCHING_ATTRIBUTE);
  });

  afterEach(() => {
    Reflect.deleteProperty(document, 'startViewTransition');
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: originalMatchMedia });
  });

  it('runs the update inside the transition callback and holds the attribute until it finishes', async () => {
    const { calls } = stubStartViewTransition();
    const update = vi.fn();
    runThemeTransition(update);
    expect(calls).toHaveLength(1);
    expect(update, 'the browser calls back after the old snapshot').not.toHaveBeenCalled();
    expect(switching()).toBe(true);

    await flushMicrotasks();
    expect(update).toHaveBeenCalledTimes(1);
    calls[0].ready.resolve();
    await flushMicrotasks();
    expect(switching(), 'still cross-fading').toBe(true);

    calls[0].finished.resolve();
    await flushMicrotasks();
    expect(switching()).toBe(false);
  });

  it('runs the update directly, with no transition, under reduced motion', () => {
    const { calls } = stubStartViewTransition();
    stubReducedMotion(true);
    const update = vi.fn();
    runThemeTransition(update);
    expect(calls).toHaveLength(0);
    expect(update).toHaveBeenCalledTimes(1);
    expect(switching()).toBe(false);
  });

  it('runs the update directly where the API is missing', () => {
    const update = vi.fn();
    runThemeTransition(update);
    expect(update).toHaveBeenCalledTimes(1);
    expect(switching()).toBe(false);
  });

  it('swallows a skipped transition: no unhandled rejection, and the attribute still clears', async () => {
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on('unhandledRejection', onUnhandled);
    try {
      const { calls } = stubStartViewTransition();
      runThemeTransition(() => {
        throw new DOMException('update failed', 'InvalidStateError');
      });
      await flushMicrotasks();
      calls[0].ready.reject(new DOMException('Transition was skipped', 'AbortError'));
      calls[0].finished.reject(new DOMException('Transition was skipped', 'AbortError'));
      await flushMicrotasks();
      await flushMicrotasks();
      expect(switching()).toBe(false);
      expect(unhandled).toEqual([]);
    } finally {
      process.off('unhandledRejection', onUnhandled);
    }
  });

  it('keeps the attribute while a second, quicker toggle is still running', async () => {
    const { calls } = stubStartViewTransition();
    runThemeTransition(() => undefined);
    runThemeTransition(() => undefined);
    await flushMicrotasks();
    // The browser skips the first when the second starts; it finishes first.
    calls[0].ready.reject(new DOMException('Transition was skipped', 'AbortError'));
    calls[0].finished.resolve();
    await flushMicrotasks();
    expect(switching()).toBe(true);
    calls[1].ready.resolve();
    calls[1].finished.resolve();
    await flushMicrotasks();
    expect(switching()).toBe(false);
  });
});
