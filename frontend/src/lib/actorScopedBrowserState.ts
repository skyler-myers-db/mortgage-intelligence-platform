import { clearGenieConversationState } from './genieConversation';

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
}
