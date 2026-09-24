import { useEffect, useState } from 'react';
import { lazyWithPreload } from '../../lib/lazyPreload';
import { subscribeToasts } from '../../lib/toast';

const LazyToaster = lazyWithPreload(() =>
  import('./Toaster').then((module) => ({ default: module.Toaster })),
);

/**
 * Mounts the shell's `<Toaster/>` (audit states-07) from its own lazy chunk,
 * so the initial bundle and stylesheet do not carry the toast region.
 *
 * The chunk is fetched as soon as the shell mounts, and the Toaster renders
 * only once it has loaded, so this never suspends and never throws: a chunk
 * that fails to load leaves toasts waiting in the `lib/toast` store, and the
 * next toast raised retries the load. Nothing a toast says is the only
 * record of a write (the page and the audit ledger keep the outcome), so a
 * missing region costs feedback, never data.
 */
export function ShellToaster() {
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (loaded) return undefined;
    let active = true;
    let loading = false;
    const load = () => {
      if (loading) return;
      loading = true;
      LazyToaster.preload().then(
        () => {
          if (active) setLoaded(true);
        },
        () => {
          loading = false;
        },
      );
    };
    load();
    const unsubscribe = subscribeToasts(load);
    return () => {
      active = false;
      unsubscribe();
    };
  }, [loaded]);

  return loaded ? <LazyToaster /> : null;
}
