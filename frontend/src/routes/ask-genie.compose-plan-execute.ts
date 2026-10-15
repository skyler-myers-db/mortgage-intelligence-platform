import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { growthAgentProgressApi } from '../lib/apiClients/growthAgentProgress';
import { _newRequestId } from '../lib/apiTransport';
import { queryKeys } from '../lib/queryKeys';
import type { ComposePlanResponse, ExecutePlanRequest, GrowthAgentPlanProgressResponse } from '../types/growthAgent';

/** Governed copy for a 409 `actor_changed`: no server text, and no Compose again. */
export const ACTOR_CHANGED_MESSAGE = 'The signed-in user changed. Nothing was run.';
/** The progress poll's interval while this hook's own execute is pending. */
export const PLAN_PROGRESS_POLL_MS = 1000;

export interface ComposedPlanExecution {
  /** Post the reviewed plan with its digest. */
  run: (request: ExecutePlanRequest) => void;
  pending: boolean;
  /** The 409: the plan changed, expired or no longer passes review; nothing ran. */
  conflict: ApiError | null;
  /** Any other failure, in the server's (sanitized) words, or the governed actor-changed copy. */
  errorMessage: string | null;
  /**
   * Per-step progress of THIS hook's own pending execute (audit `genie-09`
   * part 4); null while idle, before the server reports a step, or after settle.
   */
  progress: GrowthAgentPlanProgressResponse | null;
  /** Forget the run: a later answer to it is ignored. */
  reset: () => void;
}

/**
 * Merge a run's result into the plan card the user ran it from. Only the
 * execution fields move: the displayed plan, its model endpoint, intent and
 * reasoning stay the ones the user reviewed. A result for a card that is no
 * longer shown (a new compose, an edited objective) changes nothing.
 */
export function mergeExecutedPlan(
  current: ComposePlanResponse | null,
  result: ComposePlanResponse,
  request: ExecutePlanRequest,
): ComposePlanResponse | null {
  if (!current || current.executed || current.plan_digest !== request.plan_digest) return current;
  return {
    ...current,
    executed: result.executed,
    trace: result.trace,
    plan_id: result.plan_id,
    approval_gate_step_id: result.approval_gate_step_id,
    audit_event_ids: result.audit_event_ids,
    approval_required: result.approval_required,
  };
}

interface ExecutionState {
  pending: boolean;
  error: Error | null;
  /** The pending execute's request id: the only key the progress poll reads. */
  requestId: string | null;
}

const IDLE: ExecutionState = { pending: false, error: null, requestId: null };

/**
 * Run a composed Growth Agent plan exactly as the user reviewed it (audit
 * 2026-09-21 `critic-01`): `POST /api/growth-agent/agent/plan/execute` with
 * the displayed plan and the server's digest. The server never composes a new
 * plan; a mismatch is a 409 and nothing runs.
 *
 * Pessimistic: `onExecuted` fires only with the server's answer, and an
 * answer to a run that was reset (a new compose, an edited objective) is
 * dropped. No automatic retry: each Run is one explicit, audited user action
 * (the transport's own warm-up retry is safe, see the backend executor note).
 * A promise chain, not try/finally, so the React Compiler compiles it. It is
 * a plain hook rather than TanStack `useMutation` on purpose: that observer
 * would add a shared chunk to the /ask-genie route closure, whose budget this
 * change is capped against.
 *
 * Audit `genie-09` part 4 (W5d): `run` mints the request id and posts it, and
 * while THAT execute is pending a 1 s poll reads its own progress
 * (`GET /growth-agent/agent/plan/progress`, audit-free). The poll stops on
 * settle and on reset, never retries, is never cached past its observer and
 * never persists; a failed poll is ignored (the card stays indeterminate).
 */
export function useComposedPlanExecution({
  onExecuted,
}: {
  onExecuted: (result: ComposePlanResponse, request: ExecutePlanRequest) => void;
}): ComposedPlanExecution {
  const [state, setState] = useState<ExecutionState>(IDLE);
  const latest = useRef(0);

  function run(request: ExecutePlanRequest) {
    latest.current += 1;
    const id = latest.current;
    const posted: ExecutePlanRequest = { ...request, request_id: request.request_id ?? _newRequestId() };
    setState({ pending: true, error: null, requestId: posted.request_id ?? null });
    api.executeComposedGrowthAgentPlan(posted).then(
      (result) => {
        if (id !== latest.current) return;
        setState(IDLE);
        onExecuted(result, request);
      },
      (error: unknown) => {
        if (id !== latest.current) return;
        setState({
          pending: false,
          error: error instanceof Error ? error : new Error('The plan could not be run.'),
          requestId: null,
        });
      },
    );
  }

  function reset() {
    latest.current += 1;
    setState(IDLE);
  }

  const polling = state.pending ? state.requestId : null;
  const progressQuery = useQuery({
    queryKey: queryKeys.growthAgentPlanProgress(polling ?? ''),
    queryFn: ({ signal }) => growthAgentProgressApi.planProgress(polling ?? '', signal),
    enabled: polling !== null,
    refetchInterval: PLAN_PROGRESS_POLL_MS,
    retry: false,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const reported = polling !== null ? progressQuery.data : undefined;

  const { error } = state;
  // C6 (actor binding): the signed-in user changed between compose and run.
  // Governed copy, never the server's text, and no Compose again.
  const actorChanged = error instanceof ApiError && error.reason === 'actor_changed';
  const conflict = !actorChanged && error instanceof ApiError && error.status === 409 ? error : null;
  return {
    run,
    pending: state.pending,
    conflict,
    errorMessage: actorChanged
      ? ACTOR_CHANGED_MESSAGE
      : error && !conflict
        ? error.message || 'The plan could not be run.'
        : null,
    progress: reported && reported.state !== 'not_started' ? reported : null,
    reset,
  };
}
