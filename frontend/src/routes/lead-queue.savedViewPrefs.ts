/**
 * The actor's Lead Queue saved-view preferences (tables-05 step 2 plan
 * text, W5e item 2b): which saved view is their DEFAULT (its column state
 * applies when a queue URL names none, lead-queue.columns.ts) and the views
 * they opened most recently.
 *
 * A PRIVATE_LOCAL key of lib/actorScope ('mip.leadQueue.savedViews.v1'),
 * read and written only through its guarded accessors: nothing is read
 * before the actor is known, another actor's preferences are removed (never
 * adopted), and a foreign stamp reads as empty. JSON
 * {v: 1, defaultView: {id, name, params} | null, recent: [{id, name, params}]}
 * with at most three recent entries, most recent first. A value that does
 * not parse reads as empty and is not rewritten by the read. The snapshot is
 * cached and dropped on every write and every actor-scope event.
 *
 * Writes happen only in the Saved views panel (an explicit "Use as my
 * default", following a view's link, the panel's own successful list read
 * and a delete). Nothing here reads the network: the saved views themselves
 * (names and canonical params, which hold masked ids at most) are the
 * actor's server rows, and this key only remembers which of them to prefer.
 */
import { useSyncExternalStore } from 'react';
import { readActorScoped, subscribeActorScope, updateActorScoped } from '../lib/actorScope';

export const SAVED_VIEW_PREFS_KEY = 'mip.leadQueue.savedViews.v1';
export const RECENT_SAVED_VIEWS_MAX = 3;

export interface SavedViewRef {
  id: string;
  name: string;
  /** The view's canonical query, no leading `?`. */
  params: string;
}

export interface SavedViewPrefs {
  defaultView: SavedViewRef | null;
  recent: readonly SavedViewRef[];
}

export const EMPTY_SAVED_VIEW_PREFS: SavedViewPrefs = Object.freeze({ defaultView: null, recent: Object.freeze([]) });

function isRef(value: unknown): value is SavedViewRef {
  if (typeof value !== 'object' || value === null) return false;
  const ref = value as Record<string, unknown>;
  return typeof ref.id === 'string' && typeof ref.name === 'string' && typeof ref.params === 'string';
}

function ref(value: SavedViewRef): SavedViewRef {
  return { id: value.id, name: value.name, params: value.params };
}

/** The stored preferences, or EMPTY when absent or not this format. */
export function parseSavedViewPrefs(raw: string | null): SavedViewPrefs {
  if (!raw) return EMPTY_SAVED_VIEW_PREFS;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null || (parsed as { v?: unknown }).v !== 1) return EMPTY_SAVED_VIEW_PREFS;
    const { defaultView, recent } = parsed as { defaultView?: unknown; recent?: unknown };
    return {
      defaultView: isRef(defaultView) ? ref(defaultView) : null,
      recent: Array.isArray(recent) ? recent.filter(isRef).map(ref).slice(0, RECENT_SAVED_VIEWS_MAX) : [],
    };
  } catch {
    return EMPTY_SAVED_VIEW_PREFS;
  }
}

function serialize(prefs: SavedViewPrefs): string | null {
  if (prefs.defaultView === null && prefs.recent.length === 0) return null;
  return JSON.stringify({ v: 1, defaultView: prefs.defaultView, recent: prefs.recent });
}

let cache: SavedViewPrefs | null = null;
const subscribers = new Set<() => void>();

function invalidate(): void {
  cache = null;
  subscribers.forEach((notify) => notify());
}

subscribeActorScope(invalidate);

function subscribe(notify: () => void): () => void {
  subscribers.add(notify);
  return () => {
    subscribers.delete(notify);
  };
}

export function readSavedViewPrefs(): SavedViewPrefs {
  cache ??= parseSavedViewPrefs(readActorScoped('local', SAVED_VIEW_PREFS_KEY));
  return cache;
}

/** The actor's preferences; re-renders on every write and actor-scope event. */
export function useSavedViewPrefs(): SavedViewPrefs {
  return useSyncExternalStore(subscribe, readSavedViewPrefs, () => EMPTY_SAVED_VIEW_PREFS);
}

function update(next: (prefs: SavedViewPrefs) => SavedViewPrefs): void {
  updateActorScoped('local', SAVED_VIEW_PREFS_KEY, (raw) => serialize(next(parseSavedViewPrefs(raw))));
  invalidate();
}

/** Make `view` the default ("Use as my default"), or clear it with null. */
export function setDefaultSavedView(view: SavedViewRef | null): void {
  update((prefs) => ({ ...prefs, defaultView: view === null ? null : ref(view) }));
}

/** Remember a followed view, most recent first, at most three. */
export function recordRecentSavedView(view: SavedViewRef): void {
  update((prefs) => ({
    ...prefs,
    recent: [ref(view), ...prefs.recent.filter((entry) => entry.id !== view.id)].slice(0, RECENT_SAVED_VIEWS_MAX),
  }));
}

/** A deleted view leaves both the default and the recent list. */
export function forgetSavedView(id: string): void {
  update((prefs) => ({
    defaultView: prefs.defaultView?.id === id ? null : prefs.defaultView,
    recent: prefs.recent.filter((entry) => entry.id !== id),
  }));
}

function sameRefs(a: readonly SavedViewRef[], b: readonly SavedViewRef[]): boolean {
  return a.length === b.length && a.every((entry, index) => {
    const other = b[index];
    return other !== undefined && entry.id === other.id && entry.name === other.name && entry.params === other.params;
  });
}

/**
 * After the panel's list read succeeds: drop entries whose view is no longer
 * listed and refresh the names and params of the rest. Writes only on a change.
 */
export function reconcileSavedViewPrefs(listed: readonly SavedViewRef[]): void {
  const current = readSavedViewPrefs();
  const byId = new Map(listed.map((view) => [view.id, view]));
  const fresh = (entry: SavedViewRef | null) => (entry === null ? null : byId.get(entry.id) ?? null);
  const defaultView = fresh(current.defaultView);
  const recent = current.recent.map(fresh).filter((entry): entry is SavedViewRef => entry !== null);
  const before = current.defaultView === null ? [] : [current.defaultView];
  const after = defaultView === null ? [] : [defaultView];
  if (sameRefs(before, after) && sameRefs(current.recent, recent)) return;
  update(() => ({ defaultView: defaultView === null ? null : ref(defaultView), recent: recent.map(ref) }));
}
