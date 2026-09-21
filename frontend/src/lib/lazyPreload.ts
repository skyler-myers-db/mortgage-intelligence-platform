import {
  createElement,
  lazy,
  useState,
  type ComponentType,
  type FunctionComponent,
  type LazyExoticComponent,
} from 'react';
import { ChunkLoadError } from './chunkLoadError';

export type PreloadableComponent<T extends ComponentType<unknown>> = FunctionComponent & {
  preload: () => Promise<{ default: T }>;
};

let renderBlockedLoads = 0;

/**
 * True while at least one mounted component is suspended on a chunk import
 * (as opposed to a speculative hover / idle `preload()`). The stale-chunk
 * listener only reloads the page for failures the person is waiting on.
 */
export function hasRenderBlockedChunkLoad(): boolean {
  return renderBlockedLoads > 0;
}

/**
 * `React.lazy` with two fixes (2026-09-21 audit, `shell-v2`):
 *
 * 1. No skeleton flash after a preload. `React.lazy` suspends once even when
 *    its import already resolved, and each route mounts under a fresh
 *    Suspense boundary, so idle/hover preloading still painted the route
 *    fallback. The resolved module is cached here and rendered synchronously;
 *    `React.lazy` is only the path for a module that has not loaded yet.
 *
 * 2. A rejected preload is retryable. The failed promise is dropped, so the
 *    next `preload()` or the first render runs a fresh `import()` instead of
 *    replaying a cached rejection.
 *
 * Which path a mount uses is decided once per mount (`useState`), so a
 * committed route never swaps element type underneath itself and remounts.
 *
 * A render-path rejection is NOT retried in place: `React.lazy` caches it and
 * replacing the lazy object on remount would re-import in a tight loop while
 * React retries the failed render. That case belongs to the ErrorBoundary
 * ("Reload"); a later successful `preload()` still heals the route because
 * the synchronous path bypasses the dead lazy object.
 */
export function lazyWithPreload<T extends ComponentType<unknown>>(
  loader: () => Promise<{ default: T }>,
): PreloadableComponent<T> {
  let resolved: T | null = null;
  let promise: Promise<{ default: T }> | null = null;
  let lazyImpl: LazyExoticComponent<ComponentType<unknown>> | null = null;

  const load = () => {
    promise ??= loader()
      .then((mod) => {
        // A `vite:preloadError` listener that calls preventDefault() makes
        // Vite resolve the failed import to `undefined` instead of rejecting.
        if (!mod || typeof mod.default === 'undefined') throw new ChunkLoadError();
        resolved = mod.default;
        return mod;
      })
      .catch((err: unknown) => {
        promise = null;
        throw err;
      });
    return promise;
  };

  const loadForRender = () => {
    renderBlockedLoads += 1;
    return load().finally(() => {
      renderBlockedLoads -= 1;
    });
  };

  const currentImpl = (): ComponentType<unknown> => {
    if (resolved) return resolved;
    lazyImpl ??= lazy<ComponentType<unknown>>(loadForRender);
    return lazyImpl;
  };

  const Component: PreloadableComponent<T> = Object.assign(
    function Preloadable() {
      const [Impl] = useState(currentImpl);
      return createElement(Impl);
    },
    { preload: load },
  );
  return Component;
}

export function preloadBestEffort(preload: () => Promise<unknown>): void {
  void preload().catch(() => {
    // Speculative preload is a performance hint. A transient chunk
    // failure must not become an unhandled rejection or alter later
    // explicit navigation/open behavior; lazyWithPreload clears failed
    // cached promises so the real path can retry.
  });
}
