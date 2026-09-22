import { reportClientError } from './clientErrorLog';

/**
 * Stale-chunk recovery.
 *
 * A deploy re-hashes most lazy chunks. A tab opened before the deploy then
 * asks for a chunk that no longer exists; Vite's preload helper dispatches a
 * cancelable `vite:preloadError` on `window` before it rejects the import.
 * `index.html` is served `no-store`, so ONE reload lands on the new build.
 *
 * Reload rules, all of which must hold:
 *
 *   1. A render is actually blocked on the chunk (the person clicked a route
 *      or opened a panel). A speculative hover / idle preload that fails must
 *      never yank the page out from under an unsent draft.
 *   2. The browser does not report itself offline: reloading while offline
 *      swaps a working shell for the browser's own error page.
 *   3. No reload was already spent in the last `STALE_CHUNK_RELOAD_WINDOW_MS`
 *      (sessionStorage timestamp). If the reload did not fix it, the chunk is
 *      really gone and looping would only hammer the server. When
 *      sessionStorage is unavailable there is no way to guard, so no reload.
 *
 * Only when we reload do we `preventDefault()`, which stops Vite throwing
 * while the page tears down. In every other case the event is left alone so
 * the import rejects normally: speculative preloads swallow it, and a
 * blocked render surfaces the ErrorBoundary's "Reload" surface instead.
 */

export const STALE_CHUNK_RELOAD_KEY = 'mip.staleChunkReloadAt';
export const STALE_CHUNK_RELOAD_WINDOW_MS = 10 * 60 * 1000;

type ReloadStorage = Pick<Storage, 'getItem' | 'setItem'>;

export interface StaleChunkRecoveryDeps {
  storage: () => ReloadStorage | null;
  now: () => number;
  reload: () => void;
  isOnline: () => boolean;
  isRenderBlocked: () => boolean;
}

/** Spend the one guarded reload. Returns true when a reload was started. */
export function claimStaleChunkReload(deps: Pick<StaleChunkRecoveryDeps, 'storage' | 'now'>): boolean {
  try {
    const storage = deps.storage();
    if (!storage) return false;
    const now = deps.now();
    const last = Number(storage.getItem(STALE_CHUNK_RELOAD_KEY));
    if (Number.isFinite(last) && last > 0 && now - last < STALE_CHUNK_RELOAD_WINDOW_MS) {
      return false;
    }
    storage.setItem(STALE_CHUNK_RELOAD_KEY, String(now));
    // Read back: a quota / privacy mode that silently drops the write would
    // leave the next load unguarded.
    return storage.getItem(STALE_CHUNK_RELOAD_KEY) === String(now);
  } catch {
    return false;
  }
}

export function handlePreloadError(event: Event, deps: StaleChunkRecoveryDeps): boolean {
  const payload = (event as Event & { payload?: unknown }).payload;
  reportClientError('preload', payload);
  if (!deps.isRenderBlocked() || !deps.isOnline()) return false;
  if (!claimStaleChunkReload(deps)) return false;
  event.preventDefault();
  deps.reload();
  return true;
}

function browserSessionStorage(): ReloadStorage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function installStaleChunkRecovery(
  overrides: Partial<StaleChunkRecoveryDeps> & Pick<StaleChunkRecoveryDeps, 'isRenderBlocked'>,
): () => void {
  const deps: StaleChunkRecoveryDeps = {
    storage: browserSessionStorage,
    now: () => Date.now(),
    reload: () => window.location.reload(),
    isOnline: () => window.navigator.onLine !== false,
    ...overrides,
  };
  const listener = (event: Event) => {
    handlePreloadError(event, deps);
  };
  window.addEventListener('vite:preloadError', listener);
  return () => window.removeEventListener('vite:preloadError', listener);
}
