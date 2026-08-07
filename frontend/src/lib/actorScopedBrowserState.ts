import { clearGenieConversationState } from './genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY, clearGenieTurns } from './genieConversationStore';
import { clearPinnedInsights } from './pinnedInsights';

export const ACTOR_SCOPED_LOCAL_STORAGE_KEYS = ['mip.lastBorrowerId'] as const;
export const ACTOR_SCOPED_SESSION_STORAGE_KEYS = [
  'mip.bulkApprove.lastCancelled',
  // Genie transcript. Actor-scoped: one operator's questions and answers must
  // never survive into another operator's session on a shared booth machine.
  // Imported rather than duplicated so the key cannot drift from the store.
  GENIE_CONVERSATION_TURNS_KEY,
] as const;

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
}
