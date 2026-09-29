/**
 * @vitest-environment happy-dom
 *
 * The launcher signal before the panel's first mount (audit 2026-09-21
 * `genie-02` item 2, Genie residual #2), on the REAL in-flight store with
 * the api boundary mocked: a resumed turn rings only once revealed, a settle
 * out of sight badges the launcher with its outcome, a reset or a turn that
 * ends with no settle goes idle, and a mounted panel's claim silences it.
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
const routeAsk = vi.hoisted(() => ({ visible: false }));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: { genieSubmit: mocks.genieSubmit, genieProgress: mocks.genieProgress, genieComplete: mocks.genieComplete },
}));

vi.mock('./apiClients/genieJobs', () => ({
  genieJobsApi: { genieCompleteAsync: mocks.genieCompleteAsync, genieJobStatus: mocks.genieJobStatus },
}));

vi.mock('../components/mortgage/useGenieAnnouncer', () => ({
  isGenieRouteAskVisible: () => routeAsk.visible,
}));

import { GENIE_CONVERSATION_RESET_EVENT, GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import { clearGenieTurns } from './genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  startGenieTurn,
  stopGenieTurn,
} from './genieInFlightTurn';
import { __resetGenieLauncherSignalForTests, ensureGenieLauncherSignal, resumeGenieTurnFromSession } from './genieLauncherSignal';
import {
  claimGenieLauncherStatus,
  genieLauncherStatusText,
  getGenieLauncherOutcome,
  getGenieTurnStatus,
  setGenieTurnStatus,
  subscribeGenieTurnStatus,
} from './genieTurnStatus';

const QUESTION = 'Which states have the most prime refi candidates?';
const JOB_ID = '0a1b2c3d-0000-4000-8000-000000000001';
const IDS = { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' };

const LIVE_SUBMIT: GenieSubmitResultWithJobs = {
  completed: false,
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  progress_token: 'tok-1',
  question_hash: 'hash-1',
};

function progress(): GenieLiveProgress {
  return {
    status: 'EXECUTING_QUERY',
    stage: 'executing',
    stage_label: 'Running the governed query',
    terminal: false,
    failed: false,
    reasoning_trace: [],
    sql_preview: null,
    error_hint: null,
  };
}

function answer(source = 'genie'): GenieAnswer {
  return {
    answer: 'Illinois leads with 3,080 candidates.',
    question: QUESTION,
    source,
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
  };
}

function runningJob(): GenieCompletionJobStatus {
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
  } as GenieCompletionJobStatus;
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

const unsubscribers: Array<() => void> = [];

/** Every status the signal wrote, in order (unsubscribed after the test). */
function recordStatuses(): string[] {
  const seen: string[] = [];
  unsubscribers.push(subscribeGenieTurnStatus(() => seen.push(getGenieTurnStatus())));
  return seen;
}

async function advance(ms = 0): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

function start(): void {
  startGenieTurn({ question: QUESTION, conversationId: null, surface: 'route', startedAt: Date.now() });
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  routeAsk.visible = false;
  installStorage();
  clearGenieTurns();
  __resetGenieTurnStoreForTests();
  __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
  setGenieTurnStatus('idle');
  ensureGenieLauncherSignal();
});

afterEach(() => {
  unsubscribers.splice(0).forEach((unsubscribe) => unsubscribe());
  __resetGenieLauncherSignalForTests();
  __resetGenieTurnStoreForTests();
  clearGenieTurns();
  setGenieTurnStatus('idle');
  vi.useRealTimers();
});

describe('genieLauncherSignal', () => {
  it('a resumed turn stays idle until its first 200 reveals it, then rings', async () => {
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
      }),
    );
    let answerJob: (job: GenieCompletionJobStatus) => void = () => undefined;
    mocks.genieJobStatus.mockImplementationOnce(() => new Promise((resolve) => {
      answerJob = resolve;
    }));
    mocks.genieJobStatus.mockImplementation(() => new Promise(() => undefined));

    resumeGenieTurnFromSession();
    await advance();
    expect(mocks.genieJobStatus).toHaveBeenCalledTimes(1);
    expect(getGenieTurnStatus()).toBe('idle');

    answerJob(runningJob());
    await advance();
    expect(getGenieTurnStatus()).toBe('running');
  });

  it('a settle while the route\'s Ask tab is visible ends idle', async () => {
    routeAsk.visible = true;
    const seen = recordStatuses();
    mocks.genieSubmit.mockResolvedValue({ completed: true, response: answer() });
    start();
    await advance();
    expect(seen).toContain('running');
    expect(getGenieTurnStatus()).toBe('idle');
  });

  it('a withheld settle out of sight shows ready with the "finished" text; a reset returns idle', async () => {
    mocks.genieSubmit.mockResolvedValue({ completed: true, response: answer('refused') });
    start();
    await advance();
    expect(getGenieTurnStatus()).toBe('ready');
    expect(getGenieLauncherOutcome()).toBe('withheld');
    expect(genieLauncherStatusText(getGenieTurnStatus(), getGenieLauncherOutcome())).toBe(
      'Genie finished your question. Open Genie to see the result.',
    );

    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    expect(getGenieTurnStatus()).toBe('idle');
  });

  it('an answered settle out of sight shows ready; a turn stopped with no settle goes idle', async () => {
    mocks.genieSubmit.mockResolvedValueOnce({ completed: true, response: answer() });
    start();
    await advance();
    expect(getGenieTurnStatus()).toBe('ready');
    expect(getGenieLauncherOutcome()).toBe('answered');

    mocks.genieSubmit.mockResolvedValueOnce(LIVE_SUBMIT);
    mocks.genieProgress.mockImplementation(() => new Promise(() => undefined));
    start();
    await advance();
    expect(getGenieTurnStatus()).toBe('running');
    stopGenieTurn();
    expect(getGenieTurnStatus()).toBe('idle');
  });

  it('writes nothing while a mounted panel holds the claim', async () => {
    const release = claimGenieLauncherStatus();
    const seen = recordStatuses();
    mocks.genieSubmit.mockResolvedValue({ completed: true, response: answer() });
    start();
    await advance();
    window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    expect(seen).toEqual([]);
    expect(getGenieTurnStatus()).toBe('idle');
    release();

    mocks.genieProgress.mockResolvedValue(progress());
    mocks.genieSubmit.mockResolvedValue(LIVE_SUBMIT);
    start();
    await advance();
    expect(getGenieTurnStatus()).toBe('running');
  });
});
