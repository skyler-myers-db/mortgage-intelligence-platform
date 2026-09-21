/**
 * @vitest-environment happy-dom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  STALE_CHUNK_RELOAD_KEY,
  STALE_CHUNK_RELOAD_WINDOW_MS,
  installStaleChunkRecovery,
} from './staleChunkRecovery';

function preloadErrorEvent(): Event {
  const event = new Event('vite:preloadError', { cancelable: true });
  (event as Event & { payload?: unknown }).payload = new TypeError(
    'Failed to fetch dynamically imported module: /assets/lead-queue-0ld.js',
  );
  return event;
}

type GuardStorage = Pick<Storage, 'getItem' | 'setItem'>;

function memoryStorage(): GuardStorage & { values: Map<string, string> } {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

describe('stale-chunk recovery', () => {
  let uninstall: () => void = () => undefined;
  let reload: ReturnType<typeof vi.fn<() => void>>;
  let now: number;

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    reload = vi.fn<() => void>();
    now = 1_800_000_000_000;
  });

  afterEach(() => {
    uninstall();
    vi.restoreAllMocks();
  });

  function install(overrides: Partial<Parameters<typeof installStaleChunkRecovery>[0]> = {}) {
    const storage = memoryStorage();
    uninstall = installStaleChunkRecovery({
      storage: () => storage,
      now: () => now,
      reload,
      isOnline: () => true,
      isRenderBlocked: () => true,
      ...overrides,
    });
    return storage;
  }

  it('reloads once and cancels the event so Vite does not throw mid-teardown', () => {
    const storage = install();
    const event = preloadErrorEvent();

    window.dispatchEvent(event);

    expect(reload).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
    expect(storage.values.get(STALE_CHUNK_RELOAD_KEY)).toBe(String(now));
  });

  it('cannot loop: a second failure inside the window does not reload and is left to reject', () => {
    install();
    window.dispatchEvent(preloadErrorEvent());
    now += 5_000;
    const second = preloadErrorEvent();

    window.dispatchEvent(second);

    expect(reload).toHaveBeenCalledTimes(1);
    // Not cancelled: the import must reject so the ErrorBoundary shows "Reload".
    expect(second.defaultPrevented).toBe(false);
  });

  it('survives the reload itself: the guard is read back from storage, not module state', () => {
    const storage = install();
    window.dispatchEvent(preloadErrorEvent());
    uninstall();

    // Simulate the page coming back after the reload with the same sessionStorage.
    const reloadAfter = vi.fn<() => void>();
    uninstall = installStaleChunkRecovery({
      storage: () => storage,
      now: () => now + 1_000,
      reload: reloadAfter,
      isOnline: () => true,
      isRenderBlocked: () => true,
    });
    window.dispatchEvent(preloadErrorEvent());

    expect(reloadAfter).not.toHaveBeenCalled();
  });

  it('allows a fresh reload once the window has elapsed (a later deploy in the same tab)', () => {
    install();
    window.dispatchEvent(preloadErrorEvent());
    now += STALE_CHUNK_RELOAD_WINDOW_MS + 1;

    window.dispatchEvent(preloadErrorEvent());

    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('ignores a speculative hover / idle preload failure', () => {
    install({ isRenderBlocked: () => false });
    const event = preloadErrorEvent();

    window.dispatchEvent(event);

    expect(reload).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it('does not reload while the browser is offline', () => {
    install({ isOnline: () => false });

    window.dispatchEvent(preloadErrorEvent());

    expect(reload).not.toHaveBeenCalled();
  });

  const unguardableStorage: Array<[label: string, storage: () => GuardStorage | null]> = [
    ['sessionStorage is unavailable', () => null],
    [
      'sessionStorage throws',
      (): GuardStorage => {
        throw new Error('SecurityError');
      },
    ],
    [
      'the guard write is silently dropped',
      () => ({ getItem: () => null, setItem: () => undefined }),
    ],
  ];

  it.each(unguardableStorage)('does not reload when %s, because the loop guard cannot hold', (_label, storage) => {
    install({ storage });

    window.dispatchEvent(preloadErrorEvent());

    expect(reload).not.toHaveBeenCalled();
  });

  it('stops listening after uninstall', () => {
    install();
    uninstall();

    window.dispatchEvent(preloadErrorEvent());

    expect(reload).not.toHaveBeenCalled();
  });
});
