import { afterEach, describe, expect, it, vi } from 'vitest';
import { createIdlePreloader, saveDataRequested } from './prefetch';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('createIdlePreloader', () => {
  it('is SSR-safe when window is unavailable', () => {
    vi.stubGlobal('window', undefined);
    const loader = vi.fn<() => Promise<unknown>>(() => Promise.resolve());

    const cancel = createIdlePreloader(loader)();
    cancel();

    expect(loader).not.toHaveBeenCalled();
  });

  it('uses requestIdleCallback and is idempotent while scheduled', () => {
    const callbacks: Array<() => void> = [];
    const cancelIdleCallback = vi.fn();
    vi.stubGlobal('window', {
      requestIdleCallback: (cb: () => void) => {
        callbacks.push(cb);
        return callbacks.length;
      },
      cancelIdleCallback,
    });
    const loader = vi.fn<() => Promise<unknown>>(() => Promise.resolve());
    const schedule = createIdlePreloader(loader);

    const cancelA = schedule();
    const cancelB = schedule();

    expect(callbacks).toHaveLength(1);
    cancelA();
    cancelB();
    expect(cancelIdleCallback).toHaveBeenCalledTimes(1);
    expect(loader).not.toHaveBeenCalled();

    schedule();
    expect(callbacks).toHaveLength(2);
    callbacks[1]();
    schedule();
    expect(callbacks).toHaveLength(2);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  describe('saveData (audit bundle-09)', () => {
    function idleWindow(callbacks: Array<() => void>) {
      vi.stubGlobal('window', {
        requestIdleCallback: (cb: () => void) => {
          callbacks.push(cb);
          return callbacks.length;
        },
        cancelIdleCallback: vi.fn(),
      });
    }

    it('schedules nothing and returns a no-op cancel while the browser asks to save data', () => {
      const callbacks: Array<() => void> = [];
      idleWindow(callbacks);
      vi.stubGlobal('navigator', { connection: { saveData: true } });
      const loader = vi.fn<() => Promise<unknown>>(() => Promise.resolve());

      const cancel = createIdlePreloader(loader)();
      cancel();

      expect(callbacks).toHaveLength(0);
      expect(loader).not.toHaveBeenCalled();
    });

    it.each([
      ['saveData false', { connection: { saveData: false } }],
      ['no Network Information API', {}],
    ])('control: still schedules with %s', (_label, nav) => {
      const callbacks: Array<() => void> = [];
      idleWindow(callbacks);
      vi.stubGlobal('navigator', nav);
      const loader = vi.fn<() => Promise<unknown>>(() => Promise.resolve());

      createIdlePreloader(loader)();
      callbacks[0]?.();

      expect(callbacks).toHaveLength(1);
      expect(loader).toHaveBeenCalledTimes(1);
    });

    it('a navigator whose connection lookup throws reads as no saveData', () => {
      vi.stubGlobal('navigator', Object.defineProperty({}, 'connection', {
        get() {
          throw new Error('blocked');
        },
      }));
      expect(saveDataRequested()).toBe(false);
    });
  });

  it('falls back to setTimeout and swallows loader failures', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('window', {
      setTimeout,
      clearTimeout,
    });
    const loader = vi.fn<() => Promise<unknown>>(() => Promise.reject(new Error('chunk failed')));

    createIdlePreloader(loader, 25)();
    await vi.advanceTimersByTimeAsync(25);

    expect(loader).toHaveBeenCalledTimes(1);
  });
});
