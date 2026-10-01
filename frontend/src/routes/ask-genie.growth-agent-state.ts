import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import type {
  ComposePlanResponse,
  GrowthAgentCadence,
  GrowthAgentMonitor,
  GrowthAgentNotificationDraft,
  GrowthAgentRunResponse,
  GrowthAgentSegmentCode,
  GrowthAgentSegmentMode,
  GrowthAgentWorkflow,
  GrowthAgentWorkflowId,
} from '../types';
import { mergeExecutedPlan, useComposedPlanExecution } from './ask-genie.compose-plan-execute';
import { parseGrowthAgentStateInput } from './ask-genie.growth-agent.helpers';

/**
 * Run one handler's request as a promise chain: `onValue` with the answer
 * (a promise it returns, such as a refetch after a save, is awaited and its
 * failure lands in `onError` too), `onError` with any failure, then
 * `onSettled` exactly once. The same order as try / catch / finally, written
 * without try/finally so the React Compiler compiles the hook (audit
 * 2026-09-21 `runtime-03`; facebook/react#34131). `work` is a thunk so a
 * synchronous throw is caught like a rejection, as `await` inside `try` did.
 */
function settle<T>(
  work: () => Promise<T>,
  onValue: (value: T) => unknown,
  onError: (error: unknown) => void,
  onSettled: () => void,
): Promise<void> {
  return new Promise<T>((resolve) => resolve(work()))
    .then(onValue)
    .then(() => undefined, onError)
    .then(onSettled);
}

/** The `/ask-genie` tab an action was started from; its feedback renders there. */
export type GrowthAgentRunOrigin = 'workflows' | 'monitors';

/** An action in flight: where it was started and what it is doing, in lender copy. */
export interface GrowthAgentActiveRun {
  origin: GrowthAgentRunOrigin;
  label: string;
}

/** The objective and state scope a plan was composed for. */
export interface GrowthAgentComposeSnapshot {
  objective: string;
  states: string[];
}

/**
 * Growth Agent workspace state for `/ask-genie`: the reviewed workflow
 * catalog, saved watchlists, the objective / scope / cadence inputs, and the
 * run, compose and draft handlers. Moved verbatim out of `routes/ask-genie.tsx`
 * (file-size gate) so the route can compose the conversation and the Growth
 * Agent as separate surfaces; the requests, validation and pending flags are
 * unchanged.
 *
 * `runOrigin` and `activeRun` (audit 2026-09-21 `genie-09`) say which tab an
 * action was started from, so its progress, error and result render in that
 * tab rather than in a tab the user cannot see. `activeRun` is an honest
 * indeterminate state: each run is one blocking request with no server step
 * events, so the UI names the action and nothing more.
 *
 * Audit 2026-09-21 `critic-01`: compose only drafts a plan for review;
 * `executeComposedPlan` runs THAT plan, posting the displayed plan with the
 * server's digest and the objective and scope it was composed for
 * (`composeSnapshot`).
 */
export function useGrowthAgentWorkspace() {
  const [agentStateText, setAgentStateText] = useState('');
  const [agentCadence, setAgentCadence] = useState<GrowthAgentCadence>('daily');
  const [agentPrompt, setAgentPrompt] = useState(
    'Find prime refinance and listed-for-sale opportunities across current coverage.',
  );
  const [promptAgentPending, setPromptAgentPending] = useState(false);
  const [promptAgentPendingAction, setPromptAgentPendingAction] = useState<'run' | 'save' | null>(null);
  const [growthAgentPending, setGrowthAgentPending] = useState<GrowthAgentWorkflowId | null>(null);
  const [growthAgentPendingAction, setGrowthAgentPendingAction] = useState<'run' | 'save' | null>(null);
  const [monitorPending, setMonitorPending] = useState<string | null>(null);
  const [monitorDraftPending, setMonitorDraftPending] = useState<string | null>(null);
  const [customAgentPendingAction, setCustomAgentPendingAction] = useState<'run' | 'save' | null>(null);
  const [growthAgentError, setGrowthAgentError] = useState<string | null>(null);
  const [latestGrowthRun, setLatestGrowthRun] = useState<GrowthAgentRunResponse | null>(null);
  const [latestGrowthDrafts, setLatestGrowthDrafts] = useState<GrowthAgentNotificationDraft[]>([]);
  const [customSegments, setCustomSegments] = useState<GrowthAgentSegmentCode[]>(['itm', 'listed']);
  const [customMode, setCustomMode] = useState<GrowthAgentSegmentMode>('any');
  const [composePlan, setComposePlan] = useState<ComposePlanResponse | null>(null);
  const [composePending, setComposePending] = useState<'compose' | null>(null);
  const [composeSnapshot, setComposeSnapshot] = useState<GrowthAgentComposeSnapshot | null>(null);
  const [runOrigin, setRunOrigin] = useState<GrowthAgentRunOrigin>('workflows');
  const [activeRun, setActiveRun] = useState<GrowthAgentActiveRun | null>(null);
  const planExecution = useComposedPlanExecution({
    onExecuted: (result, request) => setComposePlan((current) => mergeExecutedPlan(current, result, request)),
  });

  function beginRun(origin: GrowthAgentRunOrigin, label: string) {
    setRunOrigin(origin);
    setActiveRun({ origin, label });
  }

  function clearGrowthAgentFeedback() {
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setComposePlan(null);
    planExecution.reset();
    setGrowthAgentError(null);
  }

  const growthAgentQuery = useQuery({
    queryKey: queryKeys.growthAgent(),
    queryFn: ({ signal }) => api.growthAgent(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const growthAgentCapabilitiesQuery = useQuery({
    queryKey: queryKeys.growthAgentCapabilities(),
    queryFn: ({ signal }) => api.growthAgentCapabilities(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  function runGrowthAgentWorkflow(workflow: GrowthAgentWorkflow, saveMonitor: boolean): Promise<void> {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return Promise.resolve();
    }
    setGrowthAgentPending(workflow.id);
    setGrowthAgentPendingAction(saveMonitor ? 'save' : 'run');
    beginRun('workflows', saveMonitor ? `Saving ${workflow.title} as a watchlist` : `Running ${workflow.title}`);
    setLatestGrowthRun(null);
    setGrowthAgentError(null);
    const stateSuffix = parsed.states.length > 0 ? ` - ${parsed.states.join(', ')}` : '';
    return settle(
      () => api.runGrowthAgentWorkflow(workflow.id, {
        states: parsed.states,
        save_monitor: saveMonitor,
        cadence: agentCadence,
        monitor_name: saveMonitor ? `${workflow.title}${stateSuffix}` : null,
      }),
      (result) => {
        setLatestGrowthRun(result);
        return saveMonitor ? growthAgentQuery.refetch() : undefined;
      },
      (err) => setGrowthAgentError(err instanceof Error ? err.message : 'Growth Agent workflow failed.'),
      () => {
        setGrowthAgentPending(null);
        setGrowthAgentPendingAction(null);
        setActiveRun(null);
      },
    );
  }

  function runMortgageGrowthAgentPrompt(saveMonitor: boolean): Promise<void> {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    const prompt = agentPrompt.trim();
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return Promise.resolve();
    }
    if (prompt.length < 3) {
      setLatestGrowthRun(null);
      setGrowthAgentError('Enter a borrower-growth objective for the agent.');
      return Promise.resolve();
    }
    setPromptAgentPending(true);
    setPromptAgentPendingAction(saveMonitor ? 'save' : 'run');
    beginRun('workflows', saveMonitor ? 'Saving your objective as a reviewed watchlist' : 'Planning a reviewed workflow for your objective');
    setLatestGrowthRun(null);
    setGrowthAgentError(null);
    const stateSuffix = parsed.states.length > 0 ? ` - ${parsed.states.join(', ')}` : '';
    return settle(
      () => api.runMortgageGrowthAgent({
        prompt,
        states: parsed.states,
        save_monitor: saveMonitor,
        cadence: agentCadence,
        monitor_name: saveMonitor ? `Mortgage Growth Agent${stateSuffix}` : null,
      }),
      (result) => {
        setLatestGrowthRun(result);
        return saveMonitor ? growthAgentQuery.refetch() : undefined;
      },
      (err) => setGrowthAgentError(err instanceof Error ? err.message : 'Mortgage Growth Agent failed.'),
      () => {
        setPromptAgentPending(false);
        setPromptAgentPendingAction(null);
        setActiveRun(null);
      },
    );
  }

  function composeGrowthAgentPlan(): Promise<void> {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    const objective = agentPrompt.trim();
    if (parsed.invalid.length > 0) {
      setComposePlan(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return Promise.resolve();
    }
    if (objective.length < 3) {
      setComposePlan(null);
      setGrowthAgentError('Enter a borrower-growth objective for the agent.');
      return Promise.resolve();
    }
    const snapshot: GrowthAgentComposeSnapshot = { objective, states: parsed.states };
    setComposePending('compose');
    beginRun('workflows', 'Composing a plan for your objective');
    setComposePlan(null);
    planExecution.reset();
    setGrowthAgentError(null);
    return settle(
      () => api.composeMortgageGrowthAgentPlan({
        objective,
        states: parsed.states,
      }),
      (result) => {
        setComposePlan(result);
        setComposeSnapshot(snapshot);
      },
      (err) => setGrowthAgentError(err instanceof Error ? err.message : 'Compose plan failed.'),
      () => {
        setComposePending(null);
        setActiveRun(null);
      },
    );
  }

  /**
   * Run the plan the card shows, exactly: its plan and digest with the
   * objective and scope it was composed for. Pessimistic: the card changes
   * only when the server answers (see `mergeExecutedPlan`).
   */
  function executeComposedPlan(response: ComposePlanResponse) {
    if (
      response.status !== 'composed'
      || !response.plan
      || !response.plan_digest
      || response.executed
      || !composeSnapshot
      || planExecution.pending
    ) {
      return;
    }
    planExecution.run({
      objective: composeSnapshot.objective,
      states: composeSnapshot.states,
      plan: response.plan,
      plan_digest: response.plan_digest,
    });
  }

  function runCustomGrowthAgentWorkflow(saveMonitor: boolean): Promise<void> {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return Promise.resolve();
    }
    if (customSegments.length === 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError('Choose at least one reviewed segment for the custom workflow.');
      return Promise.resolve();
    }
    setGrowthAgentPending('custom_segment_watch');
    setCustomAgentPendingAction(saveMonitor ? 'save' : 'run');
    beginRun('workflows', saveMonitor ? 'Saving the custom segment watchlist' : 'Running the custom segment workflow');
    setLatestGrowthRun(null);
    setGrowthAgentError(null);
    const stateSuffix = parsed.states.length > 0 ? ` - ${parsed.states.join(', ')}` : '';
    return settle(
      () => api.runCustomGrowthAgentWorkflow({
        states: parsed.states,
        segment_codes: customSegments,
        segment_mode: customMode,
        save_monitor: saveMonitor,
        cadence: agentCadence,
        monitor_name: saveMonitor
          ? `Custom Segment Workflow - ${customMode.toUpperCase()} - ${customSegments.join('+').toUpperCase()}${stateSuffix}`
          : null,
      }),
      (result) => {
        setLatestGrowthRun(result);
        return saveMonitor ? growthAgentQuery.refetch() : undefined;
      },
      (err) => setGrowthAgentError(err instanceof Error ? err.message : 'Custom Growth Agent workflow failed.'),
      () => {
        setGrowthAgentPending(null);
        setCustomAgentPendingAction(null);
        setActiveRun(null);
      },
    );
  }

  function rerunGrowthAgentMonitor(monitor: GrowthAgentMonitor): Promise<void> {
    setMonitorPending(monitor.monitor_id);
    beginRun('monitors', `Re-running ${monitor.name}`);
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setGrowthAgentError(null);
    return settle(
      () => api.rerunGrowthAgentMonitor(monitor.monitor_id, {}),
      (result) => {
        setLatestGrowthRun(result);
        return growthAgentQuery.refetch();
      },
      (err) => setGrowthAgentError(err instanceof Error ? err.message : 'Saved Growth Agent watchlist rerun failed.'),
      () => {
        setMonitorPending(null);
        setActiveRun(null);
      },
    );
  }

  function draftGrowthAgentMonitorNotifications(monitor: GrowthAgentMonitor): Promise<void> {
    setMonitorDraftPending(monitor.monitor_id);
    beginRun('monitors', `Drafting Slack and Teams notes for ${monitor.name}`);
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setGrowthAgentError(null);
    return settle(
      () => api.createGrowthAgentMonitorNotificationDrafts(monitor.monitor_id, {
        channels: ['slack', 'teams'],
      }),
      (drafts) => setLatestGrowthDrafts(drafts),
      (err) => setGrowthAgentError(err instanceof Error ? err.message : 'Saved watchlist draft handoff failed.'),
      () => {
        setMonitorDraftPending(null);
        setActiveRun(null);
      },
    );
  }

  function toggleCustomSegment(code: GrowthAgentSegmentCode) {
    clearGrowthAgentFeedback();
    setCustomSegments((current) => (
      current.includes(code)
        ? current.filter((item) => item !== code)
        : [...current, code]
    ));
  }

  const stateParsePreview = parseGrowthAgentStateInput(agentStateText);
  const workflows = growthAgentQuery.data?.workflows ?? growthAgentCapabilitiesQuery.data?.workflows ?? [];
  const monitors = growthAgentQuery.data?.monitors ?? growthAgentCapabilitiesQuery.data?.monitors ?? [];
  const agentBusy = growthAgentPending !== null || promptAgentPending || composePending !== null || planExecution.pending || monitorPending !== null || monitorDraftPending !== null;

  return {
    agentStateText,
    setAgentStateText,
    agentCadence,
    setAgentCadence,
    agentPrompt,
    setAgentPrompt,
    promptAgentPending,
    promptAgentPendingAction,
    growthAgentPending,
    growthAgentPendingAction,
    monitorPending,
    monitorDraftPending,
    customAgentPendingAction,
    growthAgentError,
    latestGrowthRun,
    latestGrowthDrafts,
    customSegments,
    customMode,
    setCustomMode,
    composePlan,
    composePending,
    composeSnapshot,
    planExecution,
    runOrigin,
    activeRun,
    workflowsLoading: growthAgentQuery.isPending,
    workflowsError: growthAgentQuery.error,
    stateParsePreview,
    workflows,
    monitors,
    agentBusy,
    clearGrowthAgentFeedback,
    runGrowthAgentWorkflow,
    runMortgageGrowthAgentPrompt,
    composeGrowthAgentPlan,
    executeComposedPlan,
    runCustomGrowthAgentWorkflow,
    rerunGrowthAgentMonitor,
    draftGrowthAgentMonitorNotifications,
    toggleCustomSegment,
  };
}

export type GrowthAgentWorkspace = ReturnType<typeof useGrowthAgentWorkspace>;
