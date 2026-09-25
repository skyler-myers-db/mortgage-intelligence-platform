/**
 * @vitest-environment happy-dom
 *
 * The in-flight store on completion jobs (audit 2026-09-21 `genie-01`): the
 * 202 names a job whose id is persisted before the first status poll, the
 * reload resume matrix (a job turn resumes by polling or by exactly one
 * rejoining complete; a legacy 'completing' turn and a v:1 record never
 * resume), 400/403/404 on a resumed job failing closed silently, the job's
 * stage on the in-flight progress, and a conversation reset that clears the
 * shared transcript with no surface mounted.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import type { GenieCompletionJobStatus, GenieSubmitResultWithJobs } from '../types/genieJobs';
import type { GenieLiveProgress, GenieResult } from './api';

const mocks = vi.hoisted(() => ({
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  genieCompleteAsync: vi.fn(),
  genieJobStatus: vi.fn(),
}));

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    api: { genieSubmit: mocks.genieSubmit, genieProgress: mocks.genieProgress, genieComplete: mocks.genieComplete },
  };
});

vi.mock('./apiClients/genieJobs', () => ({
  genieJobsApi: { genieCompleteAsync: mocks.genieCompleteAsync, genieJobStatus: mocks.genieJobStatus },
}));

import { ApiError } from './api';
import { GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import { appendGenieTurn, clearGenieTurns, getGenieTurns } from './genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
  startGenieTurn,
} from './genieInFlightTurn';

const QUESTION = 'Which states have the most prime refi candidates?';
const JOB_ID = '0a1b2c3d-0000-4000-8000-000000000001';
const IDS = { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' };

const JOB_SUBMIT: GenieSubmitResultWithJobs = {
  completed: false,
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  progress_token: 'tok-1',
  question_hash: 'hash-1',
  completion_jobs: true,
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

const ANSWER_FIELDS = {
  answer: 'Illinois leads with 3,080 candidates.',
  source: 'genie',
  trusted_assets: ['mip.gold.borrower_360'],
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  genie_status: 'COMPLETED',
};

/** The transcript's shape of the answer. */
function answer(): GenieAnswer {
  return { ...ANSWER_FIELDS };
}

/** The same answer as the job status carries it. */
function jobAnswer(): GenieResult {
  return { ...ANSWER_FIELDS };
}

function job(partial: Partial<GenieCompletionJobStatus> = {}): GenieCompletionJobStatus {
  return {
    kind: 'genie_completion_job',
    job_id: JOB_ID,
    status: 'running',
    stage: 'researching',
    stage_label: 'Running governed sub-analyses',
    parts_done: 3,
    parts_planned: 7,
    terminal: false,
    failed: false,
    error_hint: null,
    response: null,
    ...partial,
  };
}

const SUCCEEDED = () => job({ status: 'succeeded', stage: 'done', stage_label: 'Governed answer recorded', terminal: true, parts_done: null, parts_planned: null, response: jobAnswer() });

function storedRecord(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
  return raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
}

function storeRecord(overrides: Record<string, unknown> = {}): void {
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
      ...overrides,
    }),
  );
}

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

function holdLocks(outcome: 'held' | 'busy' = 'held'): void {
  __setGenieTurnLockForTests(() =>
    Promise.resolve(outcome === 'held' ? { kind: 'held', release: () => undefined } : { kind: 'busy' }),
  );
}

function requestsSent(): number {
  return Object.values(mocks).reduce((total, mock) => total + mock.mock.calls.length, 0);
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  installStorage();
  clearGenieTurns();
  __resetGenieTurnStoreForTests();
});

afterEach(() => {
  __resetGenieTurnStoreForTests();
  clearGenieTurns();
  vi.useRealTimers();
});

describe('a fresh job turn', () => {
  it('persists the job id when the 202 arrives, shows the server stage, and lands one answer', async () => {
    mocks.genieSubmit.mockResolvedValue(JOB_SUBMIT);
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieCompleteAsync.mockResolvedValue(job({ status: 'queued', stage: 'queued', stage_label: 'Queued for governed completion', parts_done: null, parts_planned: null }));
    const recordAtFirstPoll: Array<Record<string, unknown> | null> = [];
    mocks.genieJobStatus
      .mockImplementationOnce(() => {
        recordAtFirstPoll.push(storedRecord());
        return Promise.resolve(job());
      })
      .mockResolvedValue(SUCCEEDED());
    holdLocks();

    startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
    await advance();

    expect(recordAtFirstPoll[0]).toMatchObject({ v: 2, phase: 'completing', asyncComplete: true, jobId: JOB_ID });
    expect(getGenieTurnSnapshot().inFlight?.progress).toMatchObject({
      stage: 'complete',
      terminal: true,
      job: { stage: 'researching', stage_label: 'Running governed sub-analyses', parts_done: 3, parts_planned: 7 },
    });
    await advance(1_500);

    expect(mocks.genieSubmit).toHaveBeenCalledTimes(1);
    expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(1);
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(mocks.genieJobStatus).toHaveBeenCalledTimes(2);
    expect(getGenieTurns()).toEqual([{ question: QUESTION, response: answer() }]);
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
    expect(storedRecord()).toBeNull();
  });

  it('lands an expired job as its server hint and sends nothing more', async () => {
    mocks.genieSubmit.mockResolvedValue(JOB_SUBMIT);
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieCompleteAsync.mockResolvedValue(job({ status: 'queued', stage: 'queued' }));
    mocks.genieJobStatus.mockResolvedValue(
      job({ status: 'expired', stage: 'expired', terminal: true, failed: true, error_hint: 'This answer is no longer available here.' }),
    );
    holdLocks();

    startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
    await advance();
    const sent = requestsSent();
    await advance(10_000);

    expect(getGenieTurns()[0].response).toMatchObject({ answer: 'This answer is no longer available here.', source: 'degraded' });
    expect(requestsSent()).toBe(sent);
  });
});

describe('the reload resume matrix', () => {
  it('a completing job turn WITH its job id only polls: no submit, no complete; hidden until the first 200', async () => {
    storeRecord({ jobId: JOB_ID });
    holdLocks();
    let release: (value: GenieCompletionJobStatus) => void = () => undefined;
    mocks.genieJobStatus
      .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)))
      .mockResolvedValue(SUCCEEDED());

    resumeGenieTurnFromSession();
    await advance();
    expect(getGenieTurnSnapshot().inFlight).toMatchObject({ phase: 'completing', resumed: true, revealed: false, question: '' });
    release(job());
    await advance();
    expect(getGenieTurnSnapshot().inFlight).toMatchObject({ revealed: true, question: QUESTION });
    await advance(1_500);

    expect(mocks.genieSubmit).not.toHaveBeenCalled();
    expect(mocks.genieCompleteAsync).not.toHaveBeenCalled();
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(mocks.genieJobStatus.mock.calls[0].slice(0, 2)).toEqual([{ ...IDS, question: QUESTION }, JOB_ID]);
    expect(getGenieTurns()).toEqual([{ question: QUESTION, response: answer() }]);
  });

  it('a completing job turn WITHOUT a job id sends exactly one complete, which rejoins the job, then polls', async () => {
    storeRecord();
    holdLocks();
    mocks.genieCompleteAsync.mockResolvedValue(job());
    mocks.genieJobStatus.mockResolvedValue(SUCCEEDED());

    resumeGenieTurnFromSession();
    await advance(1_500);

    expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(1);
    expect(mocks.genieCompleteAsync.mock.calls[0][0]).toEqual({ ...IDS, question: QUESTION });
    expect(mocks.genieSubmit).not.toHaveBeenCalled();
    expect(mocks.genieJobStatus).toHaveBeenCalled();
    expect(getGenieTurns()).toHaveLength(1);
  });

  it("a 'polling' job turn polls on, then asks for a job (never the legacy complete)", async () => {
    storeRecord({ phase: 'polling' });
    holdLocks();
    mocks.genieProgress.mockResolvedValue(progress(true));
    mocks.genieCompleteAsync.mockResolvedValue(SUCCEEDED());

    resumeGenieTurnFromSession();
    await advance();

    expect(mocks.genieProgress).toHaveBeenCalledTimes(1);
    expect(mocks.genieCompleteAsync).toHaveBeenCalledTimes(1);
    expect(mocks.genieComplete).not.toHaveBeenCalled();
    expect(getGenieTurns()).toHaveLength(1);
  });

  it.each([
    ['a legacy completing turn', { asyncComplete: false }, 'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.', QUESTION],
    ['a job turn past the resume window', { startedAt: Date.now() - 15 * 60_000 }, 'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.', QUESTION],
    ['a v:1 record from before jobs', { v: 1 }, 'Interrupted by a reload before the answer arrived. Ask again to get it.', ''],
  ] as const)('%s is never resumed: no request, an interrupted note', async (_label, overrides, reason, noteQuestion) => {
    storeRecord({ jobId: JOB_ID, ...overrides });
    holdLocks();

    resumeGenieTurnFromSession();
    await advance(10_000);

    expect(requestsSent()).toBe(0);
    expect(storedRecord()).toBeNull();
    expect(getGenieTurnSnapshot().notes).toEqual([{ kind: 'interrupted', reason, question: noteQuestion, atTurnIndex: 0 }]);
  });

  it.each([400, 403, 404])('a %i on a resumed job fails closed silently: no note, no question, no transcript', async (status) => {
    storeRecord({ jobId: JOB_ID });
    holdLocks();
    mocks.genieJobStatus.mockRejectedValue(new ApiError('refused', { path: '/api/genie/message/status', status }));

    resumeGenieTurnFromSession();
    await advance(10_000);

    expect(mocks.genieJobStatus).toHaveBeenCalledTimes(1);
    expect(getGenieTurnSnapshot()).toMatchObject({ inFlight: null, notes: [] });
    expect(getGenieTurns()).toEqual([]);
    expect(storedRecord()).toBeNull();
  });

  it('a job another tab holds is not resumed', async () => {
    storeRecord({ jobId: JOB_ID });
    holdLocks('busy');

    resumeGenieTurnFromSession();
    await advance(10_000);

    expect(requestsSent()).toBe(0);
    expect(getGenieTurnSnapshot().notes.map((note) => note.reason)).toEqual(['This question is being answered in another tab.']);
  });
});

describe('the identity boundary', () => {
  it('clears the shared transcript on a reset with no surface mounted (a 403 submit leaves only its own turn)', async () => {
    appendGenieTurn('A question the previous actor asked', answer());
    mocks.genieSubmit.mockRejectedValue(new ApiError('forbidden', { path: '/api/genie/message/submit', status: 403 }));

    startGenieTurn({ question: QUESTION, conversationId: 'conv-1', surface: 'panel', startedAt: Date.now() });
    await advance();

    expect(getGenieTurns()).toEqual([
      { question: QUESTION, response: { answer: 'Genie session reset: forbidden', source: 'degraded', trusted_assets: [] } },
    ]);
  });
});
