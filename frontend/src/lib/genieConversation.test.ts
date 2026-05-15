import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  GENIE_CONVERSATION_STORAGE_KEY,
  clearGenieConversationState,
  readGenieConversationId,
  writeGenieConversationId,
} from './genieConversation';

describe('Genie conversation actor-bound storage', () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    const events = new EventTarget();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    };
    vi.stubGlobal('window', {
      localStorage: storage,
      addEventListener: events.addEventListener.bind(events),
      removeEventListener: events.removeEventListener.bind(events),
      dispatchEvent: events.dispatchEvent.bind(events),
    });
    vi.stubGlobal('localStorage', storage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('persists and clears the conversation id under one controlled key', () => {
    localStorage.clear();

    writeGenieConversationId('conv-123');
    expect(readGenieConversationId()).toBe('conv-123');
    expect(localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-123');

    clearGenieConversationState();
    expect(readGenieConversationId()).toBeNull();
  });

  it('notifies mounted Genie surfaces when actor-bound state is cleared', () => {
    const listener = vi.fn();
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, listener);
    try {
      clearGenieConversationState({ notify: true });
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, listener);
    }
  });
});
