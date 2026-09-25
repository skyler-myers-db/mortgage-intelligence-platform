/**
 * @vitest-environment happy-dom
 *
 * Stop on a job-backed turn asks the server not to record the answer (audit
 * 2026-09-21 `genie-03`). Stop itself stays synchronous and client-side: the
 * unconfirmed Stopped note shows at once, and only the server's reply
 * rewrites it (confirmed, or recorded with one announcement). A Stop before
 * the 202 named the job, in the polling phase, or on a non-job turn sends no
 * cancel; a failed cancel keeps the unconfirmed copy; a reset before the
 * reply means nothing reappears.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieCancelResult, GenieCompletionJobStatus, GenieSubmitResultWithJobs } from '../types/genieJobs';
import type { GenieLiveProgress } from './api';

const mocks = vi.hoisted(() => ({
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  genieCompleteAsync: vi.fn(),
  genieJobStatus: vi.fn(),
  genieCancel: vi.fn(),
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: { genieSubmit: mocks.genieSubmit, genieProgress: mocks.genieProgress, genieComplete: mocks.genieComplete },
}));

vi.mock('./apiClients/genieJobs', () => ({
  genieJobsApi: {
    genieCompleteAsync: mocks.genieCompleteAsync,
    genieJobStatus: mocks.genieJobStatus,
    genieCancel: mocks.genieCancel,
  },
}));

import { GENIE_CONVERSATION_RESET_EVENT, GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import { clearGenieTurns } from './genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
  startGenieTurn,
  stopGenieTurn,
} from './genieInFlightTurn';
import { GENIE_STOP_CONFIRMED_REASON, GENIE_STOP_RECORDED_REASON, GENIE_STOPPED_REASON } from './genieTurnOutcome';

const QUESTION = 'Which states have the most prime refi candidates?';
const JOB_ID = '0a1b2c3d-0000-4000-8000-000000000001';
const LABEL = '0123456789abcdef';
const IDS = { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' };

function submitted(partial: Partial<GenieSubmitResultWithJobs> = {}): GenieSubmitResultWithJobs {
  return {
    completed: false,
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    progress_token: 'tok-1',
    question_hash: LABEL,
    completion_jobs: true,
    ...partial,
  };
}

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

function running(): GenieCompletionJobStatus {
  return {
    kind: 'genie_completion_job',
    job_id: JOB_ID,
    status: 'running',
    stage: 'researching',
    stage_label: 'Running governed sub-analyses',
    parts_done: 1,
    parts_planned: 7,
    terminal: false,
    failed: false,
    error_hint: null,
    response: null,
  };
}

function never<T>(): Promise<T> {
  return new Promise<T>(() => undefined);
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (err: unknown) => void } {
  let resolve: (value: T) => void = () => undefined;
  let reject: (err: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function cancelResult(outcome: GenieCancelResult['outcome']): GenieCancelResult {
  return { kind: 'genie_completion_cancel', job_id: JOB_ID, outcome, status: 'running' };
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

async function flush(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0);
}

/** A fresh job turn whose 202 named the job and whose first poll holds. */
async function startedJobTurn(): Promise<void> {
  mocks.genieSubmit.mockResolvedValue(submitted());
  mocks.genieProgress.mockResolvedValue(progress(true));
  mocks.genieCompleteAsync.mockResolvedValue(running());
  mocks.genieJobStatus.mockImplementation(() => never());
  startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
  await flush();
  expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(1);
}

function stoppedNotes() {
  return getGenieTurnSnapshot().notes.filter((note) => note.kind === 'stopped');
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  installStorage();
  clearGenieTurns();
  __resetGenieTurnStoreForTests();
  __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
});

afterEach(() => {
  __resetGenieTurnStoreForTests();
  clearGenieTurns();
  vi.useRealTimers();
});

describe('Stop on a job turn', () => {
  it('sends one cancel with the ids, the job and the 16-hex label, never the question', async () => {
    await startedJobTurn();
    const reply = deferred<GenieCancelResult>();
    mocks.genieCancel.mockReturnValue(reply.promise);

    expect(stopGenieTurn()).toBe(QUESTION);

    expect(mocks.genieCancel).toHaveBeenCalledTimes(1);
    expect(mocks.genieCancel.mock.calls[0]).toEqual([IDS, JOB_ID, LABEL]);
    expect(JSON.stringify(mocks.genieCancel.mock.calls[0])).not.toContain(QUESTION);
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
  });

  it('shows the unconfirmed note at once and flips it only when the server confirms', async () => {
    await startedJobTurn();
    const reply = deferred<GenieCancelResult>();
    mocks.genieCancel.mockReturnValue(reply.promise);
    stopGenieTurn();
    const seq = getGenieTurnSnapshot().announcementSeq;

    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOPPED_REASON]);
    await flush();
    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOPPED_REASON]);

    reply.resolve(cancelResult('cancelled'));
    await flush();

    const [note] = stoppedNotes();
    expect(note).toMatchObject({ reason: GENIE_STOP_CONFIRMED_REASON, question: QUESTION });
    expect(getGenieTurnSnapshot().notes).toHaveLength(1);
    expect(getGenieTurnSnapshot().announcementSeq).toBe(seq);
    expect(mocks.genieJobStatus).toHaveBeenCalledTimes(1);
  });

  it('an ended job confirms the Stop the same way', async () => {
    await startedJobTurn();
    mocks.genieCancel.mockResolvedValue(cancelResult('ended'));
    stopGenieTurn();
    await flush();

    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOP_CONFIRMED_REASON]);
  });

  it('"recorded" rewrites the note and announces it exactly once', async () => {
    await startedJobTurn();
    mocks.genieCancel.mockResolvedValue(cancelResult('recorded'));
    stopGenieTurn();
    const seq = getGenieTurnSnapshot().announcementSeq;
    await flush();

    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOP_RECORDED_REASON]);
    expect(getGenieTurnSnapshot().announcement).toBe(GENIE_STOP_RECORDED_REASON);
    expect(getGenieTurnSnapshot().announcementSeq).toBe(seq + 1);
    await flush();
    expect(getGenieTurnSnapshot().announcementSeq).toBe(seq + 1);
  });

  it('a failed cancel leaves the unconfirmed note unchanged', async () => {
    await startedJobTurn();
    mocks.genieCancel.mockRejectedValue(new Error('lakebase unavailable'));
    stopGenieTurn();
    await flush();

    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOPPED_REASON]);
  });

  it('a reset before the reply lands means nothing reappears', async () => {
    await startedJobTurn();
    const reply = deferred<GenieCancelResult>();
    mocks.genieCancel.mockReturnValue(reply.promise);
    stopGenieTurn();

    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    reply.resolve(cancelResult('recorded'));
    await flush();

    expect(getGenieTurnSnapshot().notes).toEqual([]);
    expect(getGenieTurnSnapshot().announcement).toBe('');
  });
});

describe('no cancel is sent', () => {
  it('for a Stop in the polling phase', async () => {
    mocks.genieSubmit.mockResolvedValue(submitted());
    mocks.genieProgress.mockResolvedValue(progress(false));
    startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
    await flush();

    expect(stopGenieTurn()).toBe(QUESTION);
    expect(mocks.genieCancel).not.toHaveBeenCalled();
  });

  it('for a job turn stopped before its 202 named the job', async () => {
    mocks.genieSubmit.mockResolvedValue(submitted());
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieCompleteAsync.mockImplementation(() => never());
    startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
    await flush();

    expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(1);
    expect(stopGenieTurn()).toBe(QUESTION);
    expect(mocks.genieCancel).not.toHaveBeenCalled();
    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOPPED_REASON]);
  });

  it('for a turn without completion jobs', async () => {
    mocks.genieSubmit.mockResolvedValue(submitted({ completion_jobs: false }));
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieComplete.mockImplementation(() => never());
    startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
    await flush();

    expect(mocks.genieComplete).toHaveBeenCalledTimes(1);
    expect(stopGenieTurn()).toBe(QUESTION);
    expect(mocks.genieCancel).not.toHaveBeenCalled();
  });

  it('for a job turn whose submit carried no usable label', async () => {
    mocks.genieSubmit.mockResolvedValue(submitted({ question_hash: 'hash-1' }));
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieCompleteAsync.mockResolvedValue(running());
    mocks.genieJobStatus.mockImplementation(() => never());
    startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
    await flush();

    stopGenieTurn();
    expect(mocks.genieCancel).not.toHaveBeenCalled();
  });
});

describe('a reload-resumed job turn', () => {
  it('still cancels from its persisted label', async () => {
    window.sessionStorage.setItem(
      GENIE_IN_FLIGHT_TURN_KEY,
      JSON.stringify({
        v: 2,
        question: QUESTION,
        conversationId: 'conv-1',
        surface: 'route',
        startedAt: Date.now(),
        deep: true,
        phase: 'completing',
        ids: IDS,
        asyncComplete: true,
        jobId: JOB_ID,
        questionHash: LABEL,
      }),
    );
    mocks.genieJobStatus.mockResolvedValueOnce(running()).mockImplementation(() => never());
    mocks.genieCancel.mockResolvedValue(cancelResult('cancelled'));
    resumeGenieTurnFromSession();
    await flush();
    expect(getGenieTurnSnapshot().inFlight).toMatchObject({ revealed: true, question: QUESTION });

    stopGenieTurn();
    await flush();

    expect(mocks.genieCancel.mock.calls[0]).toEqual([IDS, JOB_ID, LABEL]);
    expect(stoppedNotes().map((note) => note.reason)).toEqual([GENIE_STOP_CONFIRMED_REASON]);
  });
});
