import type { QueryClient } from '@tanstack/react-query';
import { getGenieTurns, type GenieTurn } from './genieConversationStore';
import { subscribeGenieTurnSettled } from './genieInFlightTurn';
import { queryKeys } from './queryKeys';

/**
 * The conversation-switch signal and the link-cache sync (audit 2026-09-21
 * `shell-03` deep-link remainder).
 *
 * The floating panel and `/ask-genie` each hold the conversation id their
 * next follow-up targets. A History load on one surface used to leave the
 * other on the previous id: a follow-up from the route then went to the old
 * conversation under the loaded transcript, and a mounted
 * `/ask-genie/<id>` never followed. `emitGenieConversationSwitch` says
 * "this surface just adopted conversation X"; the other surface adopts it
 * (and the route follows it in the URL).
 *
 * The link cache (`genieConversationQueryKey`, staleTime Infinity) used to be
 * primed only by the route's own History load, so a follow-up, a leave and a
 * Back re-adopted the stale cached transcript. `ensureGenieLinkCacheSync`
 * keeps an EXISTING entry current after every settled turn; it never creates
 * an entry and never fetches.
 *
 * A lazy module: only the panel chunk and the route import it, never the
 * initial chunk. Client-only, no telemetry. The id grammar and the cache key
 * moved here verbatim from routes/ask-genie.deep-link.tsx, which re-exports
 * them, so every importer is unchanged.
 */

export const GENIE_CONVERSATION_ID_RE = /^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i;

export function isGenieConversationId(value: string | null | undefined): value is string {
  return typeof value === 'string' && GENIE_CONVERSATION_ID_RE.test(value);
}

/** The link's cache key: local, under the shared `mip` root. */
export function genieConversationQueryKey(conversationId: string) {
  return [...queryKeys.all, 'genie', 'session', conversationId] as const;
}

export type GenieConversationSwitchSurface = 'panel' | 'route';

export interface GenieConversationSwitchEvent {
  readonly conversationId: string;
  /** The surface that adopted the conversation. */
  readonly surface: GenieConversationSwitchSurface;
}

const switchListeners = new Set<(event: GenieConversationSwitchEvent) => void>();

/** One surface adopted `conversationId`: tell the other one. Synchronous. */
export function emitGenieConversationSwitch(event: GenieConversationSwitchEvent): void {
  for (const listener of [...switchListeners]) listener(event);
}

export function subscribeGenieConversationSwitch(listener: (event: GenieConversationSwitchEvent) => void): () => void {
  switchListeners.add(listener);
  return () => {
    switchListeners.delete(listener);
  };
}

/** Write the shared thread, as it stands now, into the link cache of `conversationId`. */
export function primeGenieLinkCache(queryClient: QueryClient, conversationId: string): void {
  const turns: GenieTurn[] = getGenieTurns();
  queryClient.setQueryData(genieConversationQueryKey(conversationId), { conversationId, turns });
}

let syncedClient: QueryClient | null = null;
let stopSync: (() => void) | null = null;

/**
 * Idempotent singleton (the genieLauncherSignal precedent): after every
 * settled turn whose conversation was persisted, refresh that conversation's
 * link entry from the shared thread, but only when the entry already holds
 * data. Deferred one microtask: the store appends the settled turn right
 * after it notifies, so the write reads the thread WITH it.
 */
export function ensureGenieLinkCacheSync(queryClient: QueryClient): void {
  if (syncedClient === queryClient) return;
  stopSync?.();
  syncedClient = queryClient;
  stopSync = subscribeGenieTurnSettled(({ persistedConversationId }) => {
    if (!persistedConversationId) return;
    queueMicrotask(() => {
      if (queryClient.getQueryData(genieConversationQueryKey(persistedConversationId)) === undefined) return;
      primeGenieLinkCache(queryClient, persistedConversationId);
    });
  });
}

/** Test helper: drop the singleton's subscription. */
export function _resetGenieLinkCacheSyncForTests(): void {
  stopSync?.();
  stopSync = null;
  syncedClient = null;
}
