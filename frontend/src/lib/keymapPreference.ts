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
 * preferences, so the choice lives in browser storage under one key: the
 * ACTOR_PREFERENCE_LOCAL map of lib/actorScope ({actorKey | '~nobody':
 * 'on' | 'off'}, eight entries at most). Each operator reads their own entry,
 * so one operator's choice never carries over to the next on a shared
 * machine, and returning to a machine keeps it. Storage can be unavailable
 * (private mode): the gate keeps the value in memory for the document.
 */
import { useSyncExternalStore } from 'react';
import { readActorPreference, subscribeActorScope, writeActorPreference } from './actorScope';

export const SINGLE_KEY_SHORTCUTS_STORAGE_KEY = 'mip.shortcuts.singleKey';

let cached: boolean | null = null;
const subscribers = new Set<() => void>();

function readStored(): boolean {
  return readActorPreference(SINGLE_KEY_SHORTCUTS_STORAGE_KEY) !== 'off';
}

function notify(): void {
  subscribers.forEach((callback) => callback());
}

// Every actor-gate event can change whose entry applies: re-read it.
subscribeActorScope(() => {
  cached = null;
  notify();
});

/** Whether single-key (non-modifier) shortcuts are on. Default: on. */
export function singleKeyShortcutsEnabled(): boolean {
  if (cached === null) cached = readStored();
  return cached;
}

/** Set the current actor's entry (queued before the actor is known, dropped
 *  while the gate is closed); the value shown is re-read from the map. */
export function setSingleKeyShortcutsEnabled(enabled: boolean): void {
  writeActorPreference(SINGLE_KEY_SHORTCUTS_STORAGE_KEY, enabled ? 'on' : 'off');
  cached = null;
  notify();
}

/** Drop the cached value so mounted shortcuts re-read the map (tests; the
 *  store drops it itself on every gate event). The map itself is never
 *  cleared: each actor keeps their own entry. */
export function clearSingleKeyShortcutsPreference(): void {
  cached = null;
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
