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
import { parseGrowthAgentStateInput } from './ask-genie.growth-agent.helpers';

/** The `/ask-genie` tab an action was started from; its feedback renders there. */
export type GrowthAgentRunOrigin = 'workflows' | 'monitors';

/** An action in flight: where it was started and what it is doing, in lender copy. */
export interface GrowthAgentActiveRun {
  origin: GrowthAgentRunOrigin;
  label: string;
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
  const [composePending, setComposePending] = useState<'compose' | 'execute' | null>(null);
  const [runOrigin, setRunOrigin] = useState<GrowthAgentRunOrigin>('workflows');
  const [activeRun, setActiveRun] = useState<GrowthAgentActiveRun | null>(null);

  function beginRun(origin: GrowthAgentRunOrigin, label: string) {
    setRunOrigin(origin);
    setActiveRun({ origin, label });
  }

  function clearGrowthAgentFeedback() {
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setComposePlan(null);
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

  async function runGrowthAgentWorkflow(workflow: GrowthAgentWorkflow, saveMonitor: boolean) {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return;
    }
    setGrowthAgentPending(workflow.id);
    setGrowthAgentPendingAction(saveMonitor ? 'save' : 'run');
    beginRun('workflows', saveMonitor ? `Saving ${workflow.title} as a watchlist` : `Running ${workflow.title}`);
    setLatestGrowthRun(null);
    setGrowthAgentError(null);
    try {
      const stateSuffix = parsed.states.length > 0 ? ` - ${parsed.states.join(', ')}` : '';
      const result = await api.runGrowthAgentWorkflow(workflow.id, {
        states: parsed.states,
        save_monitor: saveMonitor,
        cadence: agentCadence,
        monitor_name: saveMonitor ? `${workflow.title}${stateSuffix}` : null,
      });
      setLatestGrowthRun(result);
      if (saveMonitor) {
        await growthAgentQuery.refetch();
      }
    } catch (err) {
      setGrowthAgentError(err instanceof Error ? err.message : 'Growth Agent workflow failed.');
    } finally {
      setGrowthAgentPending(null);
      setGrowthAgentPendingAction(null);
      setActiveRun(null);
    }
  }

  async function runMortgageGrowthAgentPrompt(saveMonitor: boolean) {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    const prompt = agentPrompt.trim();
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return;
    }
    if (prompt.length < 3) {
      setLatestGrowthRun(null);
      setGrowthAgentError('Enter a borrower-growth objective for the agent.');
      return;
    }
    setPromptAgentPending(true);
    setPromptAgentPendingAction(saveMonitor ? 'save' : 'run');
    beginRun('workflows', saveMonitor ? 'Saving your objective as a reviewed watchlist' : 'Planning a reviewed workflow for your objective');
    setLatestGrowthRun(null);
    setGrowthAgentError(null);
    try {
      const stateSuffix = parsed.states.length > 0 ? ` - ${parsed.states.join(', ')}` : '';
      const result = await api.runMortgageGrowthAgent({
        prompt,
        states: parsed.states,
        save_monitor: saveMonitor,
        cadence: agentCadence,
        monitor_name: saveMonitor ? `Mortgage Growth Agent${stateSuffix}` : null,
      });
      setLatestGrowthRun(result);
      if (saveMonitor) {
        await growthAgentQuery.refetch();
      }
    } catch (err) {
      setGrowthAgentError(err instanceof Error ? err.message : 'Mortgage Growth Agent failed.');
    } finally {
      setPromptAgentPending(false);
      setPromptAgentPendingAction(null);
      setActiveRun(null);
    }
  }

  async function composeGrowthAgentPlan(execute: boolean) {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    const objective = agentPrompt.trim();
    if (parsed.invalid.length > 0) {
      setComposePlan(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return;
    }
    if (objective.length < 3) {
      setComposePlan(null);
      setGrowthAgentError('Enter a borrower-growth objective for the agent.');
      return;
    }
    setComposePending(execute ? 'execute' : 'compose');
    beginRun('workflows', execute ? 'Composing and running a plan for your objective' : 'Composing a plan for your objective');
    setComposePlan(null);
    setGrowthAgentError(null);
    try {
      const result = await api.composeMortgageGrowthAgentPlan({
        objective,
        execute,
        states: parsed.states,
      });
      setComposePlan(result);
    } catch (err) {
      setGrowthAgentError(err instanceof Error ? err.message : 'Compose plan failed.');
    } finally {
      setComposePending(null);
      setActiveRun(null);
    }
  }

  async function runCustomGrowthAgentWorkflow(saveMonitor: boolean) {
    setRunOrigin('workflows');
    const parsed = parseGrowthAgentStateInput(agentStateText);
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return;
    }
    if (customSegments.length === 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError('Choose at least one reviewed segment for the custom workflow.');
      return;
    }
    setGrowthAgentPending('custom_segment_watch');
    setCustomAgentPendingAction(saveMonitor ? 'save' : 'run');
    beginRun('workflows', saveMonitor ? 'Saving the custom segment watchlist' : 'Running the custom segment workflow');
    setLatestGrowthRun(null);
    setGrowthAgentError(null);
    try {
      const stateSuffix = parsed.states.length > 0 ? ` - ${parsed.states.join(', ')}` : '';
      const result = await api.runCustomGrowthAgentWorkflow({
        states: parsed.states,
        segment_codes: customSegments,
        segment_mode: customMode,
        save_monitor: saveMonitor,
        cadence: agentCadence,
        monitor_name: saveMonitor
          ? `Custom Segment Workflow - ${customMode.toUpperCase()} - ${customSegments.join('+').toUpperCase()}${stateSuffix}`
          : null,
      });
      setLatestGrowthRun(result);
      if (saveMonitor) {
        await growthAgentQuery.refetch();
      }
    } catch (err) {
      setGrowthAgentError(err instanceof Error ? err.message : 'Custom Growth Agent workflow failed.');
    } finally {
      setGrowthAgentPending(null);
      setCustomAgentPendingAction(null);
      setActiveRun(null);
    }
  }

  async function rerunGrowthAgentMonitor(monitor: GrowthAgentMonitor) {
    setMonitorPending(monitor.monitor_id);
    beginRun('monitors', `Re-running ${monitor.name}`);
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setGrowthAgentError(null);
    try {
      const result = await api.rerunGrowthAgentMonitor(monitor.monitor_id, {});
      setLatestGrowthRun(result);
      await growthAgentQuery.refetch();
    } catch (err) {
      setGrowthAgentError(err instanceof Error ? err.message : 'Saved Growth Agent watchlist rerun failed.');
    } finally {
      setMonitorPending(null);
      setActiveRun(null);
    }
  }

  async function draftGrowthAgentMonitorNotifications(monitor: GrowthAgentMonitor) {
    setMonitorDraftPending(monitor.monitor_id);
    beginRun('monitors', `Drafting Slack and Teams notes for ${monitor.name}`);
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setGrowthAgentError(null);
    try {
      const drafts = await api.createGrowthAgentMonitorNotificationDrafts(monitor.monitor_id, {
        channels: ['slack', 'teams'],
      });
      setLatestGrowthDrafts(drafts);
    } catch (err) {
      setGrowthAgentError(err instanceof Error ? err.message : 'Saved watchlist draft handoff failed.');
    } finally {
      setMonitorDraftPending(null);
      setActiveRun(null);
    }
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
  const agentBusy = growthAgentPending !== null || promptAgentPending || composePending !== null || monitorPending !== null || monitorDraftPending !== null;

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
    runCustomGrowthAgentWorkflow,
    rerunGrowthAgentMonitor,
    draftGrowthAgentMonitorNotifications,
    toggleCustomSegment,
  };
}

export type GrowthAgentWorkspace = ReturnType<typeof useGrowthAgentWorkspace>;
