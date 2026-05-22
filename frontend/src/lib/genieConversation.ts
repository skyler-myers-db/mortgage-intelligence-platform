export const GENIE_CONVERSATION_STORAGE_KEY = 'mip.genie.conversationId';
export const GENIE_CONVERSATION_RESET_EVENT = 'mip:genie-conversation-reset';

export function readGenieConversationId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeGenieConversationId(conversationId: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(GENIE_CONVERSATION_STORAGE_KEY, conversationId);
  } catch {
    // localStorage unavailable. Non-persistence is acceptable.
  }
}

export function clearGenieConversationState({ notify = false }: { notify?: boolean } = {}): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(GENIE_CONVERSATION_STORAGE_KEY);
  } catch {
    // localStorage unavailable. Drop the persistent cleanup.
  }
  if (notify) {
    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
  }
}
