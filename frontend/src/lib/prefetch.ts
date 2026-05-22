type Cancel = () => void;

export function createIdlePreloader(loader: () => Promise<unknown>, timeout = 3000): () => Cancel {
  let completedOrRunning = false;
  let cancelScheduled: Cancel | null = null;

  return () => {
    if (completedOrRunning) return () => undefined;
    if (cancelScheduled) return cancelScheduled;
    if (typeof window === 'undefined') return () => undefined;

    const run = () => {
      cancelScheduled = null;
      completedOrRunning = true;
      void loader().catch(() => {
        // Prefetch must never affect app behavior. A later explicit
        // navigation/open will retry through the normal import path.
      });
    };

    const win = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };

    if (typeof win.requestIdleCallback === 'function') {
      const id = win.requestIdleCallback(run, { timeout });
      cancelScheduled = () => {
        if (cancelScheduled === null) return;
        win.cancelIdleCallback?.(id);
        cancelScheduled = null;
      };
      return cancelScheduled;
    }

    const id = window.setTimeout(run, Math.min(timeout, 1000));
    cancelScheduled = () => {
      if (cancelScheduled === null) return;
      window.clearTimeout(id);
      cancelScheduled = null;
    };
    return cancelScheduled;
  };
}
