import { useEffect } from 'react';
import { asQueueContext, currentQueueContext, storeQueueContext, type QueueContext } from './queueContext';

/**
 * The Lead Queue's side of the queue context (audit 2026-09-21 `shell-04`):
 * it publishes the queue on screen so a dossier opened from it can link back
 * and page through it. Split from lib/queueContext.ts, the read side the
 * topbar breadcrumbs need on every route, so this writer ships with the lazy
 * Lead Queue chunk rather than the initial one.
 */

function newEpoch(): string {
  const cryptoApi = typeof globalThis.crypto !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Record the queue the actor is looking at. Keeps the epoch of the session. */
export function publishQueueContext(input: { search: string; label: string; ids: readonly string[] }): QueueContext | null {
  const epoch = currentQueueContext()?.epoch ?? newEpoch();
  const next = asQueueContext({ epoch, search: input.search, label: input.label, ids: [...input.ids] });
  if (!next) return null;
  storeQueueContext(next);
  return next;
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

/** "IL · In the Money · +2 filters" from the queue's active filter values. */
export function queueFilterLabel(parts: ReadonlyArray<string | null | undefined | false>): string {
  return parts.filter((part): part is string => typeof part === 'string' && part.trim().length > 0).join(' · ');
}
