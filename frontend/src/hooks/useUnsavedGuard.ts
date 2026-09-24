import { useEffect, useId, useSyncExternalStore } from 'react';

/**
 * useUnsavedGuard — keep a page's unsaved work from vanishing on a route
 * leave or a tab close (2026-09-21 audit states-05).
 *
 *   useUnsavedGuard(dirty, 'Your campaign setup is not saved.');
 *
 * While `dirty` is true the hook
 *
 *   1. keeps the store's one `beforeunload` listener attached (only while
 *      some page is dirty: an always-on listener costs the page its
 *      back/forward-cache entry), so closing or reloading the tab raises the
 *      browser's own "Leave site?" prompt;
 *   2. registers the page's message in the guard store below. The shell's
 *      `<UnsavedChangesGuard/>` reads it and, under the app's data router,
 *      blocks in-app navigation (rail, Cmd-K, a Genie link, Back) behind the
 *      "Leave without saving?" dialog.
 *
 * Under a declarative router (the MemoryRouter every route unit test
 * renders) there is no data router, so nothing blocks in-app navigation and
 * the hook degrades to (1) alone. The router decision lives in the guard
 * component, never in a try/catch around a hook call.
 *
 * The store is a module store, not a context, for the same reason as
 * lib/toast: the dirty state lives in lazy routes, the blocker in the shell.
 * React Router supports one blocker per router, so several dirty pages share
 * the one blocker the guard mounts.
 */

export const DEFAULT_UNSAVED_MESSAGE =
  'Changes on this page have not been saved. Leaving discards them.';

interface GuardEntry {
  id: string;
  message: string;
}

type Listener = () => void;

let entries: readonly GuardEntry[] = [];
const listeners = new Set<Listener>();

/**
 * One listener for the whole store, attached while any page is dirty. A
 * listener per guard does not work here: the React Compiler hoists a
 * capture-free arrow to module scope, addEventListener de-duplicates the
 * shared function, and the first page to turn clean removed it for all.
 * preventDefault() raises the prompt in every current engine (Chrome 119+,
 * Firefox, Safari); the legacy `returnValue` is deprecated.
 */
function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (entries.length > 0) event.preventDefault();
}

let unloadListening = false;

function syncBeforeUnload(): void {
  if (typeof window === 'undefined') return;
  if (entries.length > 0 && !unloadListening) {
    window.addEventListener('beforeunload', onBeforeUnload);
    unloadListening = true;
  } else if (entries.length === 0 && unloadListening) {
    window.removeEventListener('beforeunload', onBeforeUnload);
    unloadListening = false;
  }
}

function publish(next: readonly GuardEntry[]): void {
  entries = next;
  syncBeforeUnload();
  for (const listener of [...listeners]) listener();
}

/** Register one dirty page; returns the unregister function (idempotent). */
export function registerUnsavedWork(id: string, message: string): () => void {
  publish([...entries.filter((entry) => entry.id !== id), { id, message }]);
  return () => {
    if (entries.some((entry) => entry.id === id)) {
      publish(entries.filter((entry) => entry.id !== id));
    }
  };
}

/** The message of the most recently dirtied page, or null when nothing is dirty. */
export function unsavedWorkMessage(): string | null {
  return entries.length > 0 ? entries[entries.length - 1].message : null;
}

export function subscribeUnsavedWork(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Subscribe a component to the current unsaved-work message (null = clean). */
export function useUnsavedWorkMessage(): string | null {
  return useSyncExternalStore(subscribeUnsavedWork, unsavedWorkMessage, unsavedWorkMessage);
}

export function useUnsavedGuard(dirty: boolean, message: string = DEFAULT_UNSAVED_MESSAGE): void {
  const id = useId();
  useEffect(() => {
    if (!dirty) return undefined;
    return registerUnsavedWork(id, message);
  }, [dirty, id, message]);
}
