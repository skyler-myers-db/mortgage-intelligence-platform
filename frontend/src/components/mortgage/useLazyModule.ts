/**
 * useLazyModule — load a component module on demand, without Suspense.
 *
 * The approve review and the bulk gate's review are needed only on an
 * explicit Approve, so they ship as their own chunks instead of growing the
 * shared LeadTable chunk. The import is awaited here rather than rendered
 * through React.lazy: a stale chunk after a deploy must surface as a plain
 * "could not load" state the table controls, not a throw into the route
 * error boundary. A loaded module is cached, so later mounts render it
 * synchronously. The table requests an approve review's draft (an audited
 * DRAFT_OUTREACH write) only once this chunk has loaded, so a chunk that
 * fails drafts nothing (useLeadTableKeyboardFlow).
 *
 * The module cache is an external store (runtime-04 slice 3(b)): every
 * hook reads it through useSyncExternalStore, so a compiled component
 * renders a module that loaded by ANY path (another hook, or a callback's
 * `load()`), never a value memoized at mount (the 5451de15 defect).
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

export interface LazyModule<T> {
  load: () => Promise<T>;
  /** The module once loaded (synchronous for every later caller). */
  current: () => T | null;
  /** Notified once when the module loads, by any path; returns the unsubscribe. */
  subscribe: (listener: () => void) => () => void;
}

export function lazyModule<T extends object>(loader: () => Promise<T | undefined>): LazyModule<T> {
  let loaded: T | null = null;
  let pending: Promise<T> | null = null;
  const listeners = new Set<() => void>();
  return {
    load: () => {
      if (loaded) return Promise.resolve(loaded);
      pending ??= loader()
        .then((module) => {
          // A vite:preloadError listener that calls preventDefault() resolves
          // the failed import to undefined instead of rejecting.
          if (!module) throw new Error('Chunk unavailable');
          loaded = module;
          for (const listener of [...listeners]) listener();
          return module;
        })
        .finally(() => {
          // Cleared either way: a fresh caller re-attempts a failed import.
          pending = null;
        });
      return pending;
    },
    current: () => loaded,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** The module when `wanted`, loading it on first need; `failed` after a rejected import. */
export function useLazyModule<T>(source: LazyModule<T>, wanted: boolean): { module: T | null; failed: boolean } {
  const module = useSyncExternalStore(source.subscribe, source.current, source.current);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!wanted || module) return undefined;
    let live = true;
    // A success lands through the store's subscription; only a failure is ours.
    source.load().then(
      () => undefined,
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [source, wanted, module]);
  // A module that loaded later by another path is no longer a failure.
  return { module, failed: failed && module === null };
}
