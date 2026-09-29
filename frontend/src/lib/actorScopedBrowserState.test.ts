import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACTOR_SCOPED_LOCAL_STORAGE_KEYS,
  ACTOR_SCOPED_SESSION_STORAGE_KEYS,
  clearActorScopedBrowserState,
} from './actorScopedBrowserState';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  GENIE_CONVERSATION_STORAGE_KEY,
  GENIE_IN_FLIGHT_TURN_KEY,
} from './genieConversation';

function storageStub() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
    clear: () => values.clear(),
  };
}

describe('actor-scoped browser state', () => {
  beforeEach(() => {
    const events = new EventTarget();
    vi.stubGlobal('window', {
      localStorage: storageStub(),
      sessionStorage: storageStub(),
      addEventListener: events.addEventListener.bind(events),
      removeEventListener: events.removeEventListener.bind(events),
      dispatchEvent: events.dispatchEvent.bind(events),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('clears persisted actor-bound keys and notifies mounted Genie surfaces', () => {
    for (const key of ACTOR_SCOPED_LOCAL_STORAGE_KEYS) {
      window.localStorage.setItem(key, 'B-0OXOBYLW8MNCK');
    }
    for (const key of ACTOR_SCOPED_SESSION_STORAGE_KEYS) {
      window.sessionStorage.setItem(key, '{"borrower_id":"B-0OXOBYLW8MNCK"}');
    }
    window.localStorage.setItem(GENIE_CONVERSATION_STORAGE_KEY, 'conv-old-actor');

    const listener = vi.fn();
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, listener);
    try {
      clearActorScopedBrowserState();
    } finally {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, listener);
    }

    for (const key of ACTOR_SCOPED_LOCAL_STORAGE_KEYS) {
      expect(window.localStorage.getItem(key)).toBeNull();
    }
    for (const key of ACTOR_SCOPED_SESSION_STORAGE_KEYS) {
      expect(window.sessionStorage.getItem(key)).toBeNull();
    }
    expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('clears the in-flight Genie turn record, so a reload after an actor change resumes nothing', () => {
    // The key is declared in the initial closure (genieConversation.ts), so
    // this cleanup never has to import the lazy turn store.
    expect(ACTOR_SCOPED_SESSION_STORAGE_KEYS).toContain(GENIE_IN_FLIGHT_TURN_KEY);
    expect(GENIE_IN_FLIGHT_TURN_KEY).toBe('mip.genie.inFlightTurn');
    window.sessionStorage.setItem(GENIE_IN_FLIGHT_TURN_KEY, '{"v":1,"phase":"polling"}');
    clearActorScopedBrowserState();
    expect(window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY)).toBeNull();
  });
});
