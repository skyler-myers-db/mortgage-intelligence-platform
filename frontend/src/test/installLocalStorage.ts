/**
 * Single responsibility: give a happy-dom test a working `window.localStorage`.
 *
 * happy-dom may define localStorage non-configurably in the full-suite worker
 * (redefining would throw) and leave it undefined in a single-file run, so
 * reuse a working one when present and install a Map-backed stub otherwise.
 * Same pattern as PinnedInsights.test.tsx.
 */
export function installLocalStorage(): void {
  try {
    if (window.localStorage && typeof window.localStorage.clear === 'function') {
      window.localStorage.clear();
      return;
    }
  } catch {
    /* fall through to define a stub */
  }
  const store = new Map<string, string>();
  try {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => { store.set(key, String(value)); },
        removeItem: (key: string) => { store.delete(key); },
        clear: () => store.clear(),
        key: (index: number) => [...store.keys()][index] ?? null,
        get length() { return store.size; },
      },
    });
  } catch {
    /* environment owns localStorage; nothing to install */
  }
}
