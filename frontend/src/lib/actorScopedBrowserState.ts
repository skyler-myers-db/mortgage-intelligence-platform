import { GENIE_IN_FLIGHT_TURN_KEY, clearGenieConversationState } from './genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY, clearGenieTurns } from './genieConversationStore';
import { SINGLE_KEY_SHORTCUTS_STORAGE_KEY, clearSingleKeyShortcutsPreference } from './keymapPreference';
import { clearPinnedInsights } from './pinnedInsights';
import { QUEUE_CONTEXT_STORAGE_KEY, clearQueueContext } from './queueContext';

export const ACTOR_SCOPED_LOCAL_STORAGE_KEYS = [
  'mip.lastBorrowerId',
  // The Console "Single-key shortcuts" choice (WCAG 2.1.4) is per actor.
  SINGLE_KEY_SHORTCUTS_STORAGE_KEY,
] as const;
export const ACTOR_SCOPED_SESSION_STORAGE_KEYS = [
  'mip.bulkApprove.lastCancelled',
  // Genie transcript. Actor-scoped: one operator's questions and answers must
  // never survive into another operator's session on a shared booth machine.
  // Imported rather than duplicated so the key cannot drift from the store.
  GENIE_CONVERSATION_TURNS_KEY,
  // The in-flight Genie turn (question, conversation id, progress token): a
  // reload after an actor change must never resume the previous actor's turn.
  GENIE_IN_FLIGHT_TURN_KEY,
  // Lead Queue context behind the dossier breadcrumbs and pager (masked ids
  // only). Clearing it also invalidates the copies history entries carry.
  QUEUE_CONTEXT_STORAGE_KEY,
] as const;

/**
 * The last trusted `actor_cache_key` (/api/health) this tab observed
 * (Genie-turn residual #3). The shell kept it only in memory, so the first
 * key after a reload always looked like "no change" and an actor switch
 * across a reload kept the previous actor's transcript, pins, in-flight turn
 * and last borrower. The key is already opaque and actor-scoped, so it is
 * safe to keep in sessionStorage; a first key that differs from it is an
 * actor change. It is deliberately NOT in the cleared lists above: the
 * shell rewrites it with the new actor's key after clearing.
 */
export const ACTOR_CACHE_KEY_STORAGE_KEY = 'mip.actorCacheKey';

export function readStoredActorCacheKey(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeActorCacheKey(key: string | null): void {
  if (typeof window === 'undefined') return;
  try {
    if (key === null) window.sessionStorage.removeItem(ACTOR_CACHE_KEY_STORAGE_KEY);
    else window.sessionStorage.setItem(ACTOR_CACHE_KEY_STORAGE_KEY, key);
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
}

export function clearActorScopedBrowserState(): void {
  if (typeof window === 'undefined') return;
  try {
    for (const key of ACTOR_SCOPED_LOCAL_STORAGE_KEYS) {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
  try {
    for (const key of ACTOR_SCOPED_SESSION_STORAGE_KEYS) {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
  clearGenieConversationState({ notify: true });
  // Also drop the in-memory transcript cache: removing the sessionStorage key
  // above does not reset the module-level copy a mounted panel is rendering.
  clearGenieTurns();
  // Personal pinned insights (Buyer-Wow #9) are actor-scoped — clear them on
  // an actor change so one operator's pins never bleed into another session.
  clearPinnedInsights();
  clearQueueContext();
  // Drop the cached single-key choice too, so mounted shortcuts re-read it.
  clearSingleKeyShortcutsPreference();
}
