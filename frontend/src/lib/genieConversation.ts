import { readActorScoped, removeActorScoped, subscribeActorScope, writeActorScoped } from './actorScope';

export const GENIE_CONVERSATION_STORAGE_KEY = 'mip.genie.conversationId';
export const GENIE_CONVERSATION_RESET_EVENT = 'mip:genie-conversation-reset';
/** sessionStorage key of the in-flight Genie turn record (lib/genieInFlightTurn).
 *  Declared here, in the initial closure, so the shell can look for a record
 *  without importing the lazy store. */
export const GENIE_IN_FLIGHT_TURN_KEY = 'mip.genie.inFlightTurn';

/** The stored Genie conversation id: a PRIVATE_LOCAL key of lib/actorScope,
 *  so null until the actor gate opens for its owner. */
export function readGenieConversationId(): string | null {
  return readActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY);
}

export function writeGenieConversationId(conversationId: string): void {
  writeActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY, conversationId);
}

export function clearGenieConversationState({ notify = false }: { notify?: boolean } = {}): void {
  removeActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY);
  if (notify && typeof window !== 'undefined') {
    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
  }
}

/**
 * The identity boundary resets every mounted Genie surface and the turn
 * store: on 'cleared' (another actor's data was removed) and, as the W5a
 * nobody bridge, on 'closed' (a trusted nobody after a real owner: a 401,
 * 403 or anonymous body). While closed the removals the reset triggers are
 * dropped by the gate, so the stored conversation survives for a same-actor
 * reopen, which re-reads it.
 */
subscribeActorScope(({ reason }) => {
  if (reason !== 'cleared' && reason !== 'closed') return;
  if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
  window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
});
