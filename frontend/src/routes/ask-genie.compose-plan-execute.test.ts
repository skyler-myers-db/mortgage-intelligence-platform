// @vitest-environment happy-dom

/**
 * The composed-plan execute hook (audit 2026-09-21 `critic-01`, `genie-09`
 * part 4): Run mints the request id and posts it; while THAT execute is
 * pending a 1 s poll reads its own progress, and it stops on settle, reset
 * and a failed execute. No poll before Run; retry off and gcTime 0. The
 * actor-binding 409 (`actor_changed`) shows governed copy and no Compose again.
 */
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ComposePlanResponse, ExecutePlanRequest, GrowthAgentPlanProgressResponse } from '../types/growthAgent';

const executeComposedGrowthAgentPlan = vi.fn();
const planProgress = vi.fn();

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { executeComposedGrowthAgentPlan: (...args: unknown[]) => executeComposedGrowthAgentPlan(...args) },
}));
vi.mock('../lib/apiClients/growthAgentProgress', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/apiClients/growthAgentProgress')>()),
  growthAgentProgressApi: { planProgress: (...args: unknown[]) => planProgress(...args) },
}));

import { ApiError } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import {
  ACTOR_CHANGED_MESSAGE,
  PLAN_PROGRESS_POLL_MS,
  useComposedPlanExecution,
  type ComposedPlanExecution,
} from './ask-genie.compose-plan-execute';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const REQUEST: ExecutePlanRequest = {
  objective: 'Compose a refi growth plan for review.',
  states: ['IL'],
  plan: {
    objective_summary: 'Screen then gate.',
    steps: [{ step_id: 'step-1', tool: 'fn_build_cohort', params: {}, rationale: '' }],
    expected_outcome: '',
    risk_notes: '',
    requires_approval: false,
  },
  plan_digest: `v1.v1.1790000000.${'A'.repeat(43)}`,
};

const RUNNING: GrowthAgentPlanProgressResponse = {
  state: 'running', step_index: 1, step_total: 1, step_tool: 'fn_build_cohort', updated_at: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let hook: ComposedPlanExecution;
const executed = vi.fn();

function Probe() {
  hook = useComposedPlanExecution({ onExecuted: executed });
  return null;
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  onlineManager.setOnline(true);
  planProgress.mockResolvedValue(RUNNING);
  client = new QueryClient();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(QueryClientProvider, { client }, createElement(Probe))));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
  vi.useRealTimers();
});

function postedRequestId(): string {
  const [request] = executeComposedGrowthAgentPlan.mock.calls[0] as [ExecutePlanRequest];
  expect(typeof request.request_id).toBe('string');
  return request.request_id as string;
}

describe('useComposedPlanExecution', () => {
  it('never polls before Run', async () => {
    await tick(5 * PLAN_PROGRESS_POLL_MS);
    expect(planProgress).not.toHaveBeenCalled();
    expect(hook.progress).toBeNull();
  });

  it('posts its own request id and polls that id about once a second while pending', async () => {
    executeComposedGrowthAgentPlan.mockReturnValue(deferred<ComposePlanResponse>().promise);
    act(() => hook.run(REQUEST));
    const requestId = postedRequestId();
    await tick(0);
    await tick(3 * PLAN_PROGRESS_POLL_MS);
    expect(planProgress.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(planProgress.mock.calls.length).toBeLessThanOrEqual(5);
    expect(planProgress.mock.calls.every(([id]) => id === requestId)).toBe(true);
    expect(hook.progress).toEqual(RUNNING);
  });

  it('a not_started answer leaves the run indeterminate', async () => {
    planProgress.mockResolvedValue({ ...RUNNING, state: 'not_started', step_index: null, step_total: null });
    executeComposedGrowthAgentPlan.mockReturnValue(deferred<ComposePlanResponse>().promise);
    act(() => hook.run(REQUEST));
    await tick(PLAN_PROGRESS_POLL_MS);
    expect(hook.pending).toBe(true);
    expect(hook.progress).toBeNull();
  });

  it.each([
    ['settle', (run: ReturnType<typeof deferred<ComposePlanResponse>>) => run.resolve({} as ComposePlanResponse)],
    ['a failed execute', (run: ReturnType<typeof deferred<ComposePlanResponse>>) => run.reject(new Error('boom'))],
    ['reset', () => hook.reset()],
  ])('stops polling on %s, and the entry is not kept', async (_label, end) => {
    const run = deferred<ComposePlanResponse>();
    executeComposedGrowthAgentPlan.mockReturnValue(run.promise);
    act(() => hook.run(REQUEST));
    const requestId = postedRequestId();
    await tick(2 * PLAN_PROGRESS_POLL_MS);
    const before = planProgress.mock.calls.length;
    expect(before).toBeGreaterThan(0);
    await act(async () => {
      end(run);
      await Promise.resolve();
    });
    await tick(5 * PLAN_PROGRESS_POLL_MS);
    expect(planProgress.mock.calls.length).toBe(before);
    expect(hook.pending).toBe(false);
    expect(hook.progress).toBeNull();
    expect(client.getQueryCache().find({ queryKey: queryKeys.growthAgentPlanProgress(requestId) })).toBeUndefined();
  });

  it('ignores a failed poll and does not retry it', async () => {
    planProgress.mockRejectedValue(new ApiError('down', { path: '/api/growth-agent/agent/plan/progress', status: 503 }));
    executeComposedGrowthAgentPlan.mockReturnValue(deferred<ComposePlanResponse>().promise);
    act(() => hook.run(REQUEST));
    await tick(0);
    const afterFirst = planProgress.mock.calls.length;
    await tick(PLAN_PROGRESS_POLL_MS / 2);
    expect(planProgress.mock.calls.length).toBe(afterFirst);
    expect(hook.progress).toBeNull();
    expect(hook.errorMessage).toBeNull();
  });

  it('an actor_changed 409 is governed copy with no Compose again', async () => {
    executeComposedGrowthAgentPlan.mockRejectedValue(
      new ApiError('server text that must not show', {
        path: '/api/growth-agent/agent/plan/execute', status: 409, reason: 'actor_changed',
      }),
    );
    act(() => hook.run(REQUEST));
    await tick(0);
    expect(hook.conflict).toBeNull();
    expect(hook.errorMessage).toBe(ACTOR_CHANGED_MESSAGE);
  });

  it('a plain 409 is still the plan conflict', async () => {
    executeComposedGrowthAgentPlan.mockRejectedValue(
      new ApiError('The reviewed plan expired.', { path: '/api/growth-agent/agent/plan/execute', status: 409 }),
    );
    act(() => hook.run(REQUEST));
    await tick(0);
    expect(hook.conflict).not.toBeNull();
    expect(hook.errorMessage).toBeNull();
  });
});
