/**
 * @vitest-environment happy-dom
 *
 * The in-flight Genie turn store (audit 2026-09-21 `runtime-01`, critic fix
 * 13): one slot per tab, a submit that is never re-POSTed, a phase-tagged
 * sessionStorage record written before each step, a reload resume that can
 * never double-submit or double-complete, the Web Lock guard, and the
 * fail-closed identity boundary.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress, GenieSubmitResult } from './api';
import type { GenieAnswer } from '../types';

const mocks = vi.hoisted(() => ({
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
}));

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    api: {
      genieSubmit: mocks.genieSubmit,
      genieProgress: mocks.genieProgress,
      genieComplete: mocks.genieComplete,
    },
  };
});

import { ApiError } from './api';
import { GENIE_CONVERSATION_RESET_EVENT, GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import { clearGenieTurns, getGenieTurns, subscribeGenieTurns } from './genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  announceGenie,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
  startGenieTurn,
  stopGenieTurn,
  subscribeGenieTurn,
  subscribeGenieTurnSettled,
  type GenieTurnLockOutcome,
} from './genieInFlightTurn';

const QUESTION = 'Which states have the most prime refi candidates?';

const LIVE_SUBMIT: GenieSubmitResult = {
  completed: false,
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  progress_token: 'tok-1',
  question_hash: 'hash-1',
};

function progress(terminal: boolean): GenieLiveProgress {
  return {
    status: terminal ? 'COMPLETED' : 'EXECUTING_QUERY',
    stage: terminal ? 'complete' : 'executing',
    stage_label: terminal ? 'Verifying the answer against its rows' : 'Running the governed query',
    terminal,
    failed: false,
    reasoning_trace: [],
    sql_preview: null,
    error_hint: null,
  };
}

function answer(overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  return {
    answer: 'Illinois leads with 3,080 candidates.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    ...overrides,
  };
}

function start(question = QUESTION): boolean {
  return startGenieTurn({ question, conversationId: null, surface: 'panel', startedAt: Date.now() });
}

function storedRecord(): { phase?: string } | null {
  const raw = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
  return raw ? (JSON.parse(raw) as { phase?: string }) : null;
}

function storeRecord(overrides: Record<string, unknown> = {}): void {
  window.sessionStorage.setItem(
    GENIE_IN_FLIGHT_TURN_KEY,
    JSON.stringify({
      v: 1,
      question: QUESTION,
      conversationId: 'conv-1',
      surface: 'route',
      startedAt: Date.now(),
      deep: false,
      phase: 'polling',
      ids: { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' },
      ...overrides,
    }),
  );
}

/** Let promise chains and due timers run (the poll loop sleeps 1.5 s). */
async function advance(ms = 0): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

function installStorage(): void {
  for (const key of ['localStorage', 'sessionStorage'] as const) {
    const values = new Map<string, string>();
    Object.defineProperty(window, key, {
      configurable: true,
      value: {
        getItem: (k: string) => values.get(k) ?? null,
        setItem: (k: string, v: string) => values.set(k, v),
        removeItem: (k: string) => values.delete(k),
        clear: () => values.clear(),
      },
    });
  }
}

const lockRelease = vi.fn();
const lockRequests = vi.fn();

function useLock(outcome: 'held' | 'busy' | 'unsupported'): void {
  __setGenieTurnLockForTests((name) => {
    lockRequests(name);
    const result: GenieTurnLockOutcome =
      outcome === 'held' ? { kind: 'held', release: lockRelease } : { kind: outcome };
    return Promise.resolve(result);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  lockRelease.mockReset();
  lockRequests.mockReset();
  installStorage();
  clearGenieTurns();
  __resetGenieTurnStoreForTests();
});

afterEach(() => {
  __resetGenieTurnStoreForTests();
  clearGenieTurns();
  vi.useRealTimers();
});

describe('one slot per tab', () => {
  it('latches synchronously: a second start sends nothing while a turn is in flight', async () => {
    mocks.genieSubmit.mockReturnValue(new Promise(() => undefined));
    expect(start()).toBe(true);
    expect(start('And by county?')).toBe(false);
    expect(startGenieTurn({ question: 'From the route', conversationId: null, surface: 'route', startedAt: 0 })).toBe(false);
    await advance();
    expect(mocks.genieSubmit).toHaveBeenCalledTimes(1);
    expect(getGenieTurnSnapshot().inFlight).toMatchObject({ question: QUESTION, surface: 'panel', phase: 'submitting' });
  });

  it('never starts an empty question', () => {
    expect(start('   ')).toBe(false);
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
  });
});

describe('the submit is never re-POSTed', () => {
  it('lands a warming-up 503 as a degraded turn with the waking copy (submits === 1)', async () => {
    mocks.genieSubmit.mockRejectedValue(
      new ApiError('warming', {
        path: '/api/genie/message/submit',
        status: 503,
        retryable: true,
        dependency: 'warehouse',
      }),
    );
    start();
    await advance(60_000);
    expect(mocks.genieSubmit).toHaveBeenCalledTimes(1);
    expect(getGenieTurns()).toEqual([
      {
        question: QUESTION,
        response: {
          answer: 'Genie is waking the SQL warehouse. Ask again in a moment.',
          source: 'degraded',
          trusted_assets: [],
        },
      },
    ]);
    expect(getGenieTurnSnapshot()).toMatchObject({
      inFlight: null,
      announcement: 'Genie could not complete this question.',
    });
    expect(storedRecord()).toBeNull();
  });

  it('lands repeated poll failures as a degraded turn without a second submit', async () => {
    mocks.genieSubmit.mockResolvedValue(LIVE_SUBMIT);
    mocks.genieProgress.mockRejectedValue(new Error('network down'));
    start();
    await advance(60_000);
    expect(mocks.genieSubmit).toHaveBeenCalledTimes(1);
    expect(mocks.genieProgress).toHaveBeenCalledTimes(4);
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(getGenieTurns()[0].response).toMatchObject({ answer: 'Genie session reset: network down', source: 'degraded' });
  });
});

describe('the persisted record', () => {
  it("is written 'submitting' before submit, 'polling' with ids, and 'completing' BEFORE the one complete", async () => {
    const phasesAtCall: Array<string | undefined> = [];
    mocks.genieSubmit.mockImplementation(() => {
      phasesAtCall.push(storedRecord()?.phase);
      return Promise.resolve(LIVE_SUBMIT);
    });
    mocks.genieProgress.mockImplementation(() => {
      phasesAtCall.push(storedRecord()?.phase);
      return Promise.resolve(progress(true));
    });
    mocks.genieComplete.mockImplementation(() => {
      phasesAtCall.push(storedRecord()?.phase);
      return Promise.resolve(answer());
    });
    useLock('held');
    start();
    await advance();
    expect(phasesAtCall).toEqual(['submitting', 'polling', 'completing']);
    expect(mocks.genieComplete).toHaveBeenCalledTimes(1);
    expect(mocks.genieComplete.mock.calls[0].slice(0, 4)).toEqual(['conv-1', 'msg-1', 'tok-1', QUESTION]);
    // Settled: the record is gone and the lock released.
    expect(storedRecord()).toBeNull();
    expect(lockRequests).toHaveBeenCalledWith('mip-genie-turn:msg-1');
    expect(lockRelease).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBe('conv-1');
  });
});

describe('reload resume', () => {
  it("resumes a 'polling' record: polls, then completes exactly once", async () => {
    storeRecord();
    useLock('held');
    mocks.genieProgress.mockResolvedValueOnce(progress(false)).mockResolvedValue(progress(true));
    mocks.genieComplete.mockResolvedValue(answer());

    resumeGenieTurnFromSession();
    // Until the first poll returns 200 the persisted question is hidden.
    expect(getGenieTurnSnapshot().inFlight).toMatchObject({ resumed: true, revealed: false, question: '' });
    await advance();
    expect(getGenieTurnSnapshot().inFlight).toMatchObject({ revealed: true, question: QUESTION });
    await advance(1_500);
    expect(mocks.genieSubmit).not.toHaveBeenCalled();
    expect(mocks.genieProgress).toHaveBeenCalledTimes(2);
    expect(mocks.genieComplete).toHaveBeenCalledTimes(1);
    expect(getGenieTurns()).toHaveLength(1);
    expect(getGenieTurns()[0].question).toBe(QUESTION);
    expect(storedRecord()).toBeNull();

    // Once per page: a second surface mounting does not resume again.
    storeRecord();
    resumeGenieTurnFromSession();
    await advance(5_000);
    expect(mocks.genieProgress).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['submitting', {}, 'Interrupted by a reload before the answer arrived. Ask again to get it.'],
    [
      'completing',
      {},
      'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.',
    ],
    ['expired polling', { startedAt: Date.now() - 6 * 60_000 }, 'Interrupted by a reload before the answer arrived. Ask again to get it.'],
  ] as const)('never resumes a %s record: 0 requests and an interrupted note', async (label, overrides, reason) => {
    storeRecord({ phase: label === 'expired polling' ? 'polling' : label, ...overrides });
    useLock('held');
    resumeGenieTurnFromSession();
    await advance(10_000);
    expect(mocks.genieSubmit).not.toHaveBeenCalled();
    expect(mocks.genieProgress).not.toHaveBeenCalled();
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(storedRecord()).toBeNull();
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
    expect(getGenieTurnSnapshot().notes).toEqual([
      { kind: 'interrupted', reason, question: QUESTION, atTurnIndex: 0 },
    ]);
    // Said through the surface announcer too, not only shown.
    expect(getGenieTurnSnapshot().announcement).toBe(reason);
  });

  it('never resumes a turn another tab holds the lock for', async () => {
    storeRecord();
    useLock('busy');
    resumeGenieTurnFromSession();
    await advance(10_000);
    expect(lockRequests).toHaveBeenCalledWith('mip-genie-turn:msg-1');
    expect(mocks.genieProgress).not.toHaveBeenCalled();
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(getGenieTurnSnapshot().notes.map((note) => note.reason)).toEqual([
      'This question is being answered in another tab.',
    ]);
    expect(getGenieTurnSnapshot().announcement).toBe('This question is being answered in another tab.');
    expect(storedRecord()).toBeNull();
  });

  it('fails closed without navigator.locks: no resume', async () => {
    // The browser requester, in happy-dom, whose navigator.locks is null.
    expect(navigator.locks ?? null).toBeNull();
    storeRecord();
    resumeGenieTurnFromSession();
    await advance(10_000);
    expect(mocks.genieProgress).not.toHaveBeenCalled();
    expect(getGenieTurnSnapshot().notes).toHaveLength(1);
    expect(getGenieTurnSnapshot().notes[0].kind).toBe('interrupted');
  });

  it('a 403 on the first resumed poll resets silently: no transcript entry, the question never shown', async () => {
    storeRecord();
    useLock('held');
    const reset = vi.fn();
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    const questions: string[] = [];
    const unsubscribe = subscribeGenieTurn(() => {
      questions.push(getGenieTurnSnapshot().inFlight?.question ?? '');
    });
    mocks.genieProgress.mockRejectedValue(
      new ApiError('forbidden', { path: '/api/genie/message/progress', status: 403 }),
    );
    try {
      resumeGenieTurnFromSession();
      await advance(10_000);
    } finally {
      unsubscribe();
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    }
    expect(mocks.genieProgress).toHaveBeenCalledTimes(1);
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(reset).toHaveBeenCalledTimes(1);
    expect(getGenieTurns()).toEqual([]);
    expect(questions.every((question) => question === '')).toBe(true);
    expect(getGenieTurnSnapshot()).toMatchObject({ inFlight: null, notes: [], announcement: '', announcedDuringTurn: null });
    expect(storedRecord()).toBeNull();
  });
});

describe('a reload cancelling the requests', () => {
  async function startCompleting(): Promise<{ fail: (err: unknown) => void }> {
    let fail: (err: unknown) => void = () => undefined;
    mocks.genieSubmit.mockResolvedValue(LIVE_SUBMIT);
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieComplete.mockImplementation(
      () => new Promise<GenieAnswer>((_, reject) => {
        fail = reject;
      }),
    );
    start();
    await advance();
    expect(storedRecord()?.phase).toBe('completing');
    return { fail: (err) => fail(err) };
  }

  it('is not the turn failing: the record stays and the next page notes the interruption', async () => {
    const { fail } = await startCompleting();
    window.dispatchEvent(new Event('pagehide'));
    // What the browser does to the page's fetches while it unloads.
    fail(new ApiError('The app could not be reached.', { path: '/api/genie/message/complete', reason: 'unreachable' }));
    await advance(10_000);
    expect(getGenieTurns()).toEqual([]);
    const record = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
    expect(JSON.parse(record ?? '{}')).toMatchObject({ phase: 'completing', question: QUESTION });

    // The next page: a fresh store over the same sessionStorage.
    __resetGenieTurnStoreForTests();
    window.sessionStorage.setItem(GENIE_IN_FLIGHT_TURN_KEY, record ?? '');
    resumeGenieTurnFromSession();
    await advance(10_000);
    expect(mocks.genieComplete).toHaveBeenCalledTimes(1);
    expect(getGenieTurnSnapshot().notes.map((note) => note.reason)).toEqual([
      'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.',
    ]);
  });

  it('a page restored from the back/forward cache handles the held failure then', async () => {
    const { fail } = await startCompleting();
    window.dispatchEvent(new Event('pagehide'));
    fail(new Error('network down'));
    await advance();
    expect(getGenieTurnSnapshot().inFlight).not.toBeNull();
    window.dispatchEvent(new Event('pageshow'));
    await advance();
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
    expect(getGenieTurns()[0].response).toMatchObject({ source: 'degraded', answer: 'Genie session reset: network down' });
    expect(storedRecord()).toBeNull();
  });
});

describe('the identity boundary and Stop', () => {
  it('the reset event aborts, removes the key, releases the lock, and a late reply lands nowhere', async () => {
    let finish: (value: GenieAnswer) => void = () => undefined;
    mocks.genieSubmit.mockResolvedValue(LIVE_SUBMIT);
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieComplete.mockImplementation(
      () => new Promise<GenieAnswer>((resolve) => {
        finish = resolve;
      }),
    );
    useLock('held');
    start();
    await advance();
    expect(storedRecord()?.phase).toBe('completing');
    const signal = mocks.genieComplete.mock.calls[0][4] as AbortSignal;

    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    expect(signal.aborted).toBe(true);
    expect(storedRecord()).toBeNull();
    expect(lockRelease).toHaveBeenCalledTimes(1);
    expect(getGenieTurnSnapshot()).toMatchObject({ inFlight: null, notes: [], announcement: '', announcedDuringTurn: null });

    finish(answer({ conversation_id: 'conv-previous-actor' }));
    await advance(10_000);
    expect(getGenieTurns()).toEqual([]);
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
  });

  it('Stop bumps the generation: a late reply is ignored and the next turn runs normally', async () => {
    let finish: (value: GenieAnswer) => void = () => undefined;
    mocks.genieSubmit.mockResolvedValueOnce(LIVE_SUBMIT);
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieComplete.mockImplementationOnce(
      () => new Promise<GenieAnswer>((resolve) => {
        finish = resolve;
      }),
    );
    start();
    await advance();
    const stoppedGeneration = getGenieTurnSnapshot().inFlight?.generation ?? -1;
    expect(stopGenieTurn()).toBe(QUESTION);
    expect(getGenieTurnSnapshot().notes).toMatchObject([{ kind: 'stopped', question: QUESTION, atTurnIndex: 0 }]);
    expect(storedRecord()).toBeNull();

    finish(answer());
    await advance(10_000);
    expect(getGenieTurns()).toEqual([]);
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();

    mocks.genieSubmit.mockResolvedValueOnce({ completed: true, response: answer({ answer: 'Next answer.' }) });
    expect(start('Which counties lead?')).toBe(true);
    expect(getGenieTurnSnapshot().inFlight?.generation).toBe(stoppedGeneration + 2);
    await advance();
    expect(getGenieTurns().map((turn) => turn.response.answer)).toEqual(['Next answer.']);
  });
});

describe('settling', () => {
  it('calls settled listeners, then appends, then clears inFlight: no emit shows neither', async () => {
    mocks.genieSubmit.mockResolvedValue({ completed: true, response: answer() });
    const order: string[] = [];
    const offSettled = subscribeGenieTurnSettled((event) => {
      order.push(`settled:${event.outcome}:${getGenieTurns().length}`);
    });
    const offTurns = subscribeGenieTurns(() => {
      order.push(`transcript:${getGenieTurns().length}:inFlight=${getGenieTurnSnapshot().inFlight !== null}`);
    });
    const offStore = subscribeGenieTurn(() => {
      const snap = getGenieTurnSnapshot();
      order.push(`store:inFlight=${snap.inFlight !== null}:turns=${getGenieTurns().length}:${snap.announcement}`);
    });
    try {
      start();
      await advance();
    } finally {
      offSettled();
      offTurns();
      offStore();
    }
    expect(order).toEqual([
      'store:inFlight=true:turns=0:',
      'settled:answered:0',
      'transcript:1:inFlight=true',
      'store:inFlight=false:turns=1:Answer ready',
    ]);
  });

  it.each([
    ['refused', 'Genie did not answer this question. The reason is shown in the thread.'],
    ['out_of_footprint', 'Genie did not answer this question. The reason is shown in the thread.'],
    ['degraded', 'Genie could not complete this question.'],
    ['trusted_sql', 'Answer ready'],
  ])('announces a %s settle honestly', async (source, spoken) => {
    mocks.genieSubmit.mockResolvedValue({ completed: true, response: answer({ source }) });
    start();
    await advance();
    expect(getGenieTurnSnapshot().announcement).toBe(spoken);
  });
});

describe('announcements', () => {
  it('numbers every announcement, a repeat included, and a reset never rewinds the count', () => {
    const before = getGenieTurnSnapshot().announcementSeq;
    announceGenie('SQL copied');
    announceGenie('SQL copied');
    expect(getGenieTurnSnapshot()).toMatchObject({ announcement: 'SQL copied', announcementSeq: before + 2 });
    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    expect(getGenieTurnSnapshot()).toMatchObject({ announcement: '', announcementSeq: before + 2 });
    __resetGenieTurnStoreForTests();
    expect(getGenieTurnSnapshot().announcementSeq).toBe(before + 2);
  });

  it('ties a mid-turn announcement to the turn and its stage; a landing one to none', async () => {
    mocks.genieSubmit.mockResolvedValue(LIVE_SUBMIT);
    mocks.genieProgress.mockResolvedValue(progress(false));
    let finish: (value: GenieAnswer) => void = () => undefined;
    mocks.genieComplete.mockImplementation(() => new Promise<GenieAnswer>((resolve) => {
      finish = resolve;
    }));
    start();
    await advance();
    const inFlight = getGenieTurnSnapshot().inFlight!;
    announceGenie('SQL copied');
    expect(getGenieTurnSnapshot().announcedDuringTurn).toEqual({
      generation: inFlight.generation,
      progress: inFlight.progress,
    });
    mocks.genieProgress.mockResolvedValue(progress(true));
    await advance(10_000);
    finish(answer());
    await advance();
    expect(getGenieTurnSnapshot()).toMatchObject({ inFlight: null, announcement: 'Answer ready', announcedDuringTurn: null });
  });
});

