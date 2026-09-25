type Cancel = () => void;

/**
 * True when the browser asks pages to save data (`Save-Data`, the Network
 * Information API's `navigator.connection.saveData`; audit bundle-09). A
 * browser without the API, or a navigator that throws, reads as false.
 */
export function saveDataRequested(): boolean {
  try {
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
    return connection?.saveData === true;
  } catch {
    return false;
  }
}

export function createIdlePreloader(loader: () => Promise<unknown>, timeout = 3000): () => Cancel {
  let completedOrRunning = false;
  let cancelScheduled: Cancel | null = null;

  return () => {
    if (completedOrRunning) return () => undefined;
    if (cancelScheduled) return cancelScheduled;
    if (typeof window === 'undefined') return () => undefined;
    // Idle preloads are speculative downloads: none while the browser asks to
    // save data (audit bundle-09). Explicit preloads (hover, a lazy render)
    // do not come through here and are unaffected.
    if (saveDataRequested()) return () => undefined;

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
