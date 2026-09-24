/**
 * The per-actor "Single-key shortcuts" preference (audit wow-power-4,
 * WCAG 2.1.4 Character Key Shortcuts).
 *
 * WCAG 2.1.4 (Level A) requires every shortcut that uses only a character
 * key to be switchable off, remappable, or active only on focus. The Lead
 * Queue keys are focus-scoped already; this switch turns EVERY binding that
 * has no Ctrl / Cmd / Alt modifier off at once (J, K, the arrows, Enter, X,
 * A, R, Shift+A, `/`, `?`). Modifier chords such as Cmd/Ctrl-K stay on.
 *
 * Persistence: the workspace API stores saved leads and drafts only, not
 * preferences, so the choice lives in browser storage under one key that
 * `clearActorScopedBrowserState` clears on an in-session actor change. One
 * operator's choice therefore never carries over to the next operator on a
 * shared machine. Storage can be unavailable (private mode): the in-memory
 * value still works for the session.
 */
import { useSyncExternalStore } from 'react';

export const SINGLE_KEY_SHORTCUTS_STORAGE_KEY = 'mip.shortcuts.singleKey';

let cached: boolean | null = null;
const subscribers = new Set<() => void>();

function readStored(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY) !== 'off';
  } catch {
    return true;
  }
}

function notify(): void {
  subscribers.forEach((callback) => callback());
}

/** Whether single-key (non-modifier) shortcuts are on. Default: on. */
export function singleKeyShortcutsEnabled(): boolean {
  if (cached === null) cached = readStored();
  return cached;
}

export function setSingleKeyShortcutsEnabled(enabled: boolean): void {
  cached = enabled;
  try {
    window.localStorage.setItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY, enabled ? 'on' : 'off');
  } catch {
    // Storage unavailable: the in-memory value still applies this session.
  }
  notify();
}

/** Actor change: forget the previous operator's choice (back to the default). */
export function clearSingleKeyShortcutsPreference(): void {
  cached = null;
  try {
    window.localStorage.removeItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY);
  } catch {
    // Storage unavailable: nothing persisted to remove.
  }
  notify();
}

function onStorage(event: StorageEvent): void {
  if (event.key !== SINGLE_KEY_SHORTCUTS_STORAGE_KEY && event.key !== null) return;
  cached = null;
  notify();
}

export function subscribeSingleKeyShortcuts(callback: () => void): () => void {
  if (subscribers.size === 0 && typeof window !== 'undefined') {
    window.addEventListener('storage', onStorage);
  }
  subscribers.add(callback);
  return () => {
    subscribers.delete(callback);
    if (subscribers.size === 0 && typeof window !== 'undefined') {
      window.removeEventListener('storage', onStorage);
    }
  };
}

/** React binding: `[enabled, setEnabled]`, re-rendering on every change. */
export function useSingleKeyShortcuts(): [boolean, (enabled: boolean) => void] {
  const enabled = useSyncExternalStore(
    subscribeSingleKeyShortcuts,
    singleKeyShortcutsEnabled,
    () => true,
  );
  return [enabled, setSingleKeyShortcutsEnabled];
}
