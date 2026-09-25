import { useRef, useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { ComposePlanResponse, ExecutePlanRequest } from '../types/growthAgent';

export interface ComposedPlanExecution {
  /** Post the reviewed plan with its digest. */
  run: (request: ExecutePlanRequest) => void;
  pending: boolean;
  /** The 409: the plan changed, expired or no longer passes review; nothing ran. */
  conflict: ApiError | null;
  /** Any other failure, in the server's (sanitized) words. */
  errorMessage: string | null;
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

const IDLE: { pending: boolean; error: Error | null } = { pending: false, error: null };

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
 */
export function useComposedPlanExecution({
  onExecuted,
}: {
  onExecuted: (result: ComposePlanResponse, request: ExecutePlanRequest) => void;
}): ComposedPlanExecution {
  const [state, setState] = useState(IDLE);
  const latest = useRef(0);

  function run(request: ExecutePlanRequest) {
    latest.current += 1;
    const id = latest.current;
    setState({ pending: true, error: null });
    api.executeComposedGrowthAgentPlan(request).then(
      (result) => {
        if (id !== latest.current) return;
        setState(IDLE);
        onExecuted(result, request);
      },
      (error: unknown) => {
        if (id !== latest.current) return;
        setState({ pending: false, error: error instanceof Error ? error : new Error('The plan could not be run.') });
      },
    );
  }

  function reset() {
    latest.current += 1;
    setState(IDLE);
  }

  const { error } = state;
  const conflict = error instanceof ApiError && error.status === 409 ? error : null;
  return {
    run,
    pending: state.pending,
    conflict,
    errorMessage: error && !conflict ? error.message || 'The plan could not be run.' : null,
    reset,
  };
}
