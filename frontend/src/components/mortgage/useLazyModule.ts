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
 */
import { useEffect, useState } from 'react';

export interface LazyModule<T> {
  load: () => Promise<T>;
  /** The module once loaded (synchronous for every later caller). */
  current: () => T | null;
}

export function lazyModule<T extends object>(loader: () => Promise<T | undefined>): LazyModule<T> {
  let loaded: T | null = null;
  let pending: Promise<T> | null = null;
  return {
    load: () => {
      if (loaded) return Promise.resolve(loaded);
      pending ??= loader()
        .then((module) => {
          // A vite:preloadError listener that calls preventDefault() resolves
          // the failed import to undefined instead of rejecting.
          if (!module) throw new Error('Chunk unavailable');
          loaded = module;
          return module;
        })
        .finally(() => {
          pending = null;
        });
      return pending;
    },
    current: () => loaded,
  };
}

/** The module when `wanted`, loading it on first need; `failed` after a rejected import. */
export function useLazyModule<T>(source: LazyModule<T>, wanted: boolean): { module: T | null; failed: boolean } {
  const [state, setState] = useState<{ module: T | null; failed: boolean }>(() => ({
    module: source.current(),
    failed: false,
  }));
  useEffect(() => {
    if (!wanted || state.module) return undefined;
    let live = true;
    source.load()
      .then((module) => {
        if (live) setState({ module, failed: false });
      })
      .catch(() => {
        if (live) setState({ module: null, failed: true });
      });
    return () => {
      live = false;
    };
  }, [source, wanted, state.module]);
  return state;
}
