/**
 * @vitest-environment happy-dom
 *
 * A reload's cancelled request is not the turn failing, in Chromium 153's
 * pagehide order too (audit 2026-09-21 `stack-10`, the Playwright 1.63
 * renderer bump). Chromium 147 ran every pagehide listener before the
 * cancelled fetch's rejection; Chromium 153 runs that rejection in a microtask
 * during the dispatch, before the lazy Genie chunk's own pagehide listener has
 * set its flag. The in-flight record must survive either way, so the next
 * page rejoins the job (genie-jobs.fixture (d)) or notes the interruption
 * (genie-turn.fixture (c)). The 147 order is pinned in genieInFlightTurn.test.ts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import type { GenieCompletionJobStatus, GenieSubmitResultWithJobs } from '../types/genieJobs';
import type { GenieLiveProgress } from './api';

const mocks = vi.hoisted(() => ({
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  genieCompleteAsync: vi.fn(),
  genieJobStatus: vi.fn(),
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: { genieSubmit: mocks.genieSubmit, genieProgress: mocks.genieProgress, genieComplete: mocks.genieComplete },
}));

vi.mock('./apiClients/genieJobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./apiClients/genieJobs')>()),
  genieJobsApi: { genieCompleteAsync: mocks.genieCompleteAsync, genieJobStatus: mocks.genieJobStatus },
}));

import { GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import { clearGenieTurns, getGenieTurns } from './genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
  startGenieTurn,
} from './genieInFlightTurn';

const QUESTION = 'Which states have the most prime refi candidates?';
const RELOAD_WHILE_VERIFYING =
  'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.';

const SUBMIT: GenieSubmitResultWithJobs = {
  completed: false,
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  progress_token: 'tok-1',
  question_hash: 'hash-1',
  completion_jobs: false,
};

const TERMINAL_PROGRESS: GenieLiveProgress = {
  status: 'COMPLETED',
  stage: 'complete',
  stage_label: 'Verifying the answer against its rows',
  terminal: true,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};

const ANSWER_FIELDS = {
  answer: 'Illinois leads with 3,080 candidates.',
  source: 'genie',
  trusted_assets: ['mip.gold.borrower_360'],
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  genie_status: 'COMPLETED',
};

/** The transcript's shape of the answer the job carries. */
const ANSWER: GenieAnswer = { ...ANSWER_FIELDS };

const SUCCEEDED_JOB: GenieCompletionJobStatus = {
  kind: 'genie_completion_job',
  job_id: '0a1b2c3d-0000-4000-8000-000000000001',
  status: 'succeeded',
  stage: 'done',
  stage_label: 'Governed answer recorded',
  parts_done: null,
  parts_planned: null,
  terminal: true,
  failed: false,
  error_hint: null,
  response: { ...ANSWER_FIELDS },
};

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

function storedRecord(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
  return raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
}

/** Let promise chains and due timers run. */
async function advance(ms = 0): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

/** Every queued promise continuation, and no timer: the rest of the task. */
async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 50; i += 1) await Promise.resolve();
}

/** Start a turn whose complete call (legacy or job) the test settles. */
async function startCompleting(asyncComplete: boolean): Promise<(err: unknown) => void> {
  let cancel: (err: unknown) => void = () => undefined;
  const held = () => new Promise<never>((_, reject) => (cancel = reject));
  mocks.genieSubmit.mockResolvedValue({ ...SUBMIT, completion_jobs: asyncComplete });
  mocks.genieProgress.mockResolvedValue(TERMINAL_PROGRESS);
  (asyncComplete ? mocks.genieCompleteAsync : mocks.genieComplete).mockImplementation(held);
  startGenieTurn({ question: QUESTION, conversationId: null, surface: 'route', startedAt: Date.now() });
  await advance();
  expect(storedRecord()).toMatchObject({ phase: 'completing', asyncComplete });
  return (err) => cancel(err);
}

/** Chromium 153: the rejection runs, THEN this chunk's pagehide listener. */
async function reloadInChromium153Order(cancel: (err: unknown) => void): Promise<string> {
  cancel(new TypeError('Failed to fetch'));
  await flushMicrotasks();
  window.dispatchEvent(new Event('pagehide'));
  await advance(10_000);
  const record = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
  return record ?? '';
}

/** This tab holds the turn's Web Lock (happy-dom has no navigator.locks). */
function holdLocks(): void {
  __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
}

/** The next page: a fresh store over the same sessionStorage. */
async function nextPage(record: string): Promise<void> {
  __resetGenieTurnStoreForTests();
  holdLocks();
  window.sessionStorage.setItem(GENIE_IN_FLIGHT_TURN_KEY, record);
  resumeGenieTurnFromSession();
  await advance(10_000);
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  installStorage();
  clearGenieTurns();
  __resetGenieTurnStoreForTests();
  holdLocks();
});

afterEach(() => {
  __resetGenieTurnStoreForTests();
  clearGenieTurns();
  vi.useRealTimers();
});

describe('a reload whose cancelled request rejects before pagehide reaches the store (Chromium 153)', () => {
  it('keeps a completing turn: no failure lands, and the next page notes the interruption', async () => {
    const cancel = await startCompleting(false);
    const record = await reloadInChromium153Order(cancel);

    expect(getGenieTurns()).toEqual([]);
    expect(getGenieTurnSnapshot()).toMatchObject({ inFlight: { phase: 'completing', question: QUESTION }, notes: [] });
    expect(JSON.parse(record || '{}')).toMatchObject({ phase: 'completing', question: QUESTION });

    await nextPage(record);
    expect(mocks.genieComplete).toHaveBeenCalledTimes(1);
    expect(getGenieTurnSnapshot().notes.map((note) => note.reason)).toEqual([RELOAD_WHILE_VERIFYING]);
  });

  it('keeps a job turn: the next page sends one more complete with the same ids and lands one answer', async () => {
    const cancel = await startCompleting(true);
    const record = await reloadInChromium153Order(cancel);

    expect(getGenieTurns()).toEqual([]);
    expect(JSON.parse(record || '{}')).toMatchObject({ phase: 'completing', asyncComplete: true });

    expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(1);
    const firstBody: unknown = mocks.genieCompleteAsync.mock.calls[0][0];
    mocks.genieCompleteAsync.mockResolvedValue(SUCCEEDED_JOB);
    await nextPage(record);
    expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(2);
    expect(mocks.genieCompleteAsync.mock.calls[1][0]).toEqual(firstBody);
    expect(mocks.genieSubmit).toHaveBeenCalledTimes(1);
    expect(getGenieTurns()).toEqual([{ question: QUESTION, response: ANSWER }]);
    expect(storedRecord()).toBeNull();
  });

  it('a network failure on a page that stays visible still lands as the turn failing', async () => {
    const cancel = await startCompleting(false);
    cancel(new TypeError('Failed to fetch'));
    await advance();

    expect(getGenieTurns()).toEqual([
      { question: QUESTION, response: { answer: 'Genie session reset: Failed to fetch', source: 'degraded', trusted_assets: [] } },
    ]);
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
    expect(storedRecord()).toBeNull();
  });
});
