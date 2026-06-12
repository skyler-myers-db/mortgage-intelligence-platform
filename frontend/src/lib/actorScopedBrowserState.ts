import { clearGenieConversationState } from './genieConversation';
import { clearPinnedInsights } from './pinnedInsights';

export const ACTOR_SCOPED_LOCAL_STORAGE_KEYS = ['mip.lastBorrowerId'] as const;
export const ACTOR_SCOPED_SESSION_STORAGE_KEYS = ['mip.bulkApprove.lastCancelled'] as const;

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
  // Personal pinned insights (Buyer-Wow #9) are actor-scoped — clear them on
  // an actor change so one operator's pins never bleed into another session.
  clearPinnedInsights();
}
