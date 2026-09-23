import { useEffect, useSyncExternalStore } from 'react';

/**
 * The canonical masked borrower id (CLAUDE.md naming rules), spelled here
 * rather than imported from routeMeta: with that import, the lazy route chunks
 * that publish and read this store (lead-queue, borrower-360) made rolldown
 * hoist ~113 KB of entry modules (react, AppContext, react-query) into a
 * common chunk that still loads on every page but escapes the initial-JS gate
 * (measured 2026-09-23; bisected to this one edge). queueContext.test.ts pins
 * the two patterns identical.
 */
export const QUEUE_MASKED_ID_RE = /^B-[0-9A-Z]{13}$/;

/**
 * Queue context — which ranked Lead Queue a dossier was opened from (audit
 * 2026-09-21 `shell-04`). Borrower 360 used to be a dead end: the crumb named
 * neither the borrower nor the origin, and there was no next lead. The Lead
 * Queue publishes its search string, a short filter label and its ordered
 * MASKED ids here; the dossier's breadcrumbs link back to that exact queue URL
 * and its pager steps through the ids.
 *
 * Carriage: React Router Link state (per history entry, so Back / Forward keep
 * each dossier's own queue) with a sessionStorage fallback (reload, a link
 * opened without state). The storage key is actor-scoped
 * (lib/actorScopedBrowserState.ts) and every context carries the storage
 * `epoch`: clearing the key on an actor change also invalidates the copies
 * that history entries still hold, because a Link state is honoured only
 * while its epoch matches the stored one.
 *
 * Only masked `B-` ids are kept; anything else is dropped on read and write.
 * Nothing here fetches: reading a context never opens (and never audits) a
 * borrower.
 */

export const QUEUE_CONTEXT_STORAGE_KEY = 'mip.queueContext';
/** More than the queue ever loads at once; bounds what history state carries. */
export const MAX_QUEUE_CONTEXT_IDS = 1000;
const MAX_LABEL_CHARS = 160;
const MAX_SEARCH_CHARS = 4000;

export interface QueueContext {
  /** Storage epoch this context belongs to (see module comment). */
  epoch: string;
  /** The Lead Queue URL search string: '' or '?...'. */
  search: string;
  /** Short filter summary, e.g. "IL · In the Money"; '' when unfiltered. */
  label: string;
  /** Masked borrower ids in queue rank order. */
  ids: readonly string[];
}

export interface QueueLinkState {
  queue: QueueContext;
}

export interface QueuePosition {
  /** 1-based position of the borrower in the queue. */
  position: number;
  total: number;
  previous: string | null;
  next: string | null;
}

function asQueueContext(value: unknown): QueueContext | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const { epoch, search, label, ids } = raw;
  if (typeof epoch !== 'string' || epoch.length === 0 || epoch.length > 64) return null;
  if (typeof search !== 'string' || search.length > MAX_SEARCH_CHARS) return null;
  if (search !== '' && !search.startsWith('?')) return null;
  if (typeof label !== 'string') return null;
  if (!Array.isArray(ids)) return null;
  const masked = ids
    .filter((id): id is string => typeof id === 'string' && QUEUE_MASKED_ID_RE.test(id))
    .slice(0, MAX_QUEUE_CONTEXT_IDS);
  return { epoch, search, label: label.slice(0, MAX_LABEL_CHARS), ids: masked };
}

function readStored(): QueueContext | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(QUEUE_CONTEXT_STORAGE_KEY);
    return raw ? asQueueContext(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

// --- one tiny external store, so a mounted Link state never goes stale ---

let snapshot: QueueContext | null | undefined;
const listeners = new Set<() => void>();

function current(): QueueContext | null {
  if (snapshot === undefined) snapshot = readStored();
  return snapshot;
}

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function newEpoch(): string {
  const cryptoApi = typeof globalThis.crypto !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Record the queue the actor is looking at. Keeps the epoch of the session. */
export function publishQueueContext(input: { search: string; label: string; ids: readonly string[] }): QueueContext | null {
  const epoch = current()?.epoch ?? newEpoch();
  const next = asQueueContext({ epoch, search: input.search, label: input.label, ids: [...input.ids] });
  if (!next) return null;
  snapshot = next;
  try {
    window.sessionStorage.setItem(QUEUE_CONTEXT_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable; the in-memory copy still serves this tab.
  }
  emit();
  return next;
}

/** Drop the in-memory copy (the actor-scoped clear removes the stored key). */
export function clearQueueContext(): void {
  snapshot = null;
  emit();
}

/** The context a history entry carries, if its epoch is still the live one. */
function fromLinkState(state: unknown, stored: QueueContext | null): QueueContext | null {
  if (!stored || !state || typeof state !== 'object') return null;
  const carried = asQueueContext((state as { queue?: unknown }).queue);
  return carried && carried.epoch === stored.epoch ? carried : null;
}

/**
 * The queue a dossier belongs to: its history entry's own context first, else
 * the last published queue when it lists this borrower; null otherwise.
 */
export function resolveQueueContext(
  locationState: unknown,
  borrowerId: string | null,
  stored: QueueContext | null = current(),
): QueueContext | null {
  const carried = fromLinkState(locationState, stored);
  if (carried && (borrowerId === null || carried.ids.includes(borrowerId))) return carried;
  if (stored && borrowerId !== null && stored.ids.includes(borrowerId)) return stored;
  return null;
}

/** React view of `resolveQueueContext`, re-read whenever a queue is published. */
export function useQueueContext(locationState: unknown, borrowerId: string | null): QueueContext | null {
  const published = useSyncExternalStore(subscribe, current, () => null);
  return resolveQueueContext(locationState, borrowerId, published);
}

/** Link state for a dossier opened from the queue that is on screen now. */
export function useQueueLinkState(): QueueLinkState | undefined {
  const published = useSyncExternalStore(subscribe, current, () => null);
  return published ? { queue: published } : undefined;
}

/** Publish the rendered queue whenever it settles (never placeholder rows). */
export function usePublishQueueContext(
  queue: { search: string; label: string; ids: readonly string[] } | null,
): void {
  const search = queue?.search ?? null;
  const label = queue?.label ?? '';
  const key = queue ? queue.ids.join(',') : null;
  useEffect(() => {
    if (search === null || key === null) return;
    publishQueueContext({ search, label, ids: key === '' ? [] : key.split(',') });
  }, [search, label, key]);
}

export function queuePosition(context: QueueContext, borrowerId: string): QueuePosition | null {
  const index = context.ids.indexOf(borrowerId);
  if (index === -1) return null;
  return {
    position: index + 1,
    total: context.ids.length,
    previous: index > 0 ? context.ids[index - 1] : null,
    next: index < context.ids.length - 1 ? context.ids[index + 1] : null,
  };
}

/** `/lead-queue?...`: the exact filtered queue the context came from. */
export function queueHref(context: QueueContext | null): `/lead-queue${string}` {
  return `/lead-queue${context?.search ?? ''}`;
}

/** "Lead Queue · IL · In the Money", or "Lead Queue" for the unfiltered queue. */
export function queueCrumbLabel(context: QueueContext | null): string {
  return context?.label ? `Lead Queue · ${context.label}` : 'Lead Queue';
}

/** "IL · In the Money · +2 filters" from the queue's active filter values. */
export function queueFilterLabel(parts: ReadonlyArray<string | null | undefined | false>): string {
  return parts.filter((part): part is string => typeof part === 'string' && part.trim().length > 0).join(' · ');
}
