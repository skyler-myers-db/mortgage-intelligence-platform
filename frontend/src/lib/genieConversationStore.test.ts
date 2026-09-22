import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import {
  GENIE_CONVERSATION_TURNS_KEY,
  MAX_STORED_TURNS,
  appendGenieTurn,
  clearGenieTurns,
  getGenieTurns,
  setGenieTurns,
  subscribeGenieTurns,
  turnsToMessages,
  type GenieChatMessage,
  type GenieTurn,
} from './genieConversationStore';

function answer(text: string): GenieAnswer {
  return { answer: text, source: 'genie', trusted_assets: ['mip.gold.borrower_360'] } as GenieAnswer;
}

function turn(question: string, text = `${question} answer`): GenieTurn {
  return { question, response: answer(text) };
}

let values: Map<string, string>;

beforeEach(() => {
  values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
    clear: () => values.clear(),
  };
  vi.stubGlobal('window', { sessionStorage: storage });
  vi.stubGlobal('sessionStorage', storage);
  // Reset the module-level cache between cases.
  clearGenieTurns();
  values.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('genie transcript store — persist / restore', () => {
  it('persists a settled turn to sessionStorage under the versioned key', () => {
    appendGenieTurn('How many borrowers are in the money?', answer('12,480 borrowers.'));

    const raw = values.get(GENIE_CONVERSATION_TURNS_KEY);
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string)).toEqual([
      { question: 'How many borrowers are in the money?', response: answer('12,480 borrowers.') },
    ]);
  });

  it('restores the full conversation — including the latest answer — after a reload', async () => {
    appendGenieTurn('first', answer('first answer'));
    appendGenieTurn('second', answer('second answer'));

    // A reload re-reads sessionStorage.
    const restored = turnsToMessages(await hydrateFresh());
    expect(restored.map((m) => (m.who === 'user' ? m.text : m.payload.answer))).toEqual([
      'first',
      'first answer',
      'second',
      'second answer',
    ]);
  });

  it('appends to the LIVE list, keeping a turn another surface settled in the meantime', () => {
    // The floating panel starts a turn; while it runs, /ask-genie settles one.
    const before = getGenieTurns();
    setGenieTurns([turn('route question')]);

    // The panel's write reads the current list at call time, not the copy it
    // held when its turn started, so the route's turn is never overwritten.
    appendGenieTurn('panel question', answer('panel answer'));

    expect(getGenieTurns().map((t) => t.question)).toEqual(['route question', 'panel question']);
    expect(getGenieTurns()).not.toBe(before);
  });

  it('caps stored turns at MAX_STORED_TURNS, dropping the oldest', () => {
    const many = Array.from({ length: MAX_STORED_TURNS + 5 }, (_, i) => turn(`q${i}`));
    setGenieTurns(many);

    const stored = JSON.parse(values.get(GENIE_CONVERSATION_TURNS_KEY) as string) as GenieTurn[];
    expect(stored).toHaveLength(MAX_STORED_TURNS);
    expect(stored[0].question).toBe('q5');
    expect(stored[stored.length - 1].question).toBe(`q${MAX_STORED_TURNS + 4}`);
  });

  it('tolerates corrupt stored JSON by starting clean', async () => {
    values.set(GENIE_CONVERSATION_TURNS_KEY, '{not json');
    await expect(hydrateFresh()).resolves.toEqual([]);
  });

  it('drops entries that are not turn-shaped', async () => {
    values.set(GENIE_CONVERSATION_TURNS_KEY, JSON.stringify([{ nope: true }, turn('good')]));
    const hydrated = await hydrateFresh();
    expect(hydrated).toHaveLength(1);
    expect(hydrated[0].question).toBe('good');
  });
});

describe('genie transcript store — new thread', () => {
  it('clears both the in-memory cache and sessionStorage', () => {
    setGenieTurns([turn('q1'), turn('q2')]);
    expect(getGenieTurns()).toHaveLength(2);

    clearGenieTurns();

    expect(getGenieTurns()).toEqual([]);
    expect(values.get(GENIE_CONVERSATION_TURNS_KEY)).toBeUndefined();
  });

  it('notifies subscribers on write and on clear', () => {
    const seen: number[] = [];
    const unsubscribe = subscribeGenieTurns(() => seen.push(getGenieTurns().length));

    setGenieTurns([turn('q1')]);
    clearGenieTurns();
    unsubscribe();
    setGenieTurns([turn('ignored')]);

    expect(seen).toEqual([1, 0]);
  });
});

describe('turn -> message adapter', () => {
  it('renders a normal alternating conversation as user/ai pairs', () => {
    const expected: GenieChatMessage[] = [
      { who: 'user', text: 'q1' },
      { who: 'ai', payload: answer('a1'), sources: undefined },
      { who: 'user', text: 'q2' },
      { who: 'ai', payload: answer('a2'), sources: undefined },
    ];
    expect(turnsToMessages([turn('q1', 'a1'), turn('q2', 'a2')])).toEqual(expected);
  });

  it('keeps an answer that has no originating question (governed action result)', () => {
    const turns: GenieTurn[] = [{ question: '', response: answer('Saved 12 borrowers.') }];
    expect(turnsToMessages(turns)).toEqual([
      { who: 'ai', payload: answer('Saved 12 borrowers.'), sources: undefined },
    ]);
  });

  it('attaches derived source assets when a resolver is supplied', () => {
    const messages = turnsToMessages([turn('q1')], (payload) => payload.trusted_assets ?? []);
    const ai = messages.find((m) => m.who === 'ai');
    expect(ai && ai.who === 'ai' ? ai.sources : null).toEqual(['mip.gold.borrower_360']);
  });
});

/**
 * Hydrate from whatever is currently in sessionStorage using a FRESH module
 * instance. The store's in-memory cache is module-level and only reads
 * storage once (`cache === null`), so a genuine "reopen the panel / reload
 * the tab" restore can only be exercised by re-importing the module.
 */
async function hydrateFresh(): Promise<GenieTurn[]> {
  vi.resetModules();
  const fresh = await import('./genieConversationStore');
  return fresh.getGenieTurns();
}
