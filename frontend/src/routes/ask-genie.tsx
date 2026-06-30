import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ApiError, api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type {
  GenieActionSuggestion,
  GenieAnswer as GenieAnswerShape,
  GrowthAgentCadence,
  GrowthAgentMonitor,
  GrowthAgentNotificationDraft,
  GrowthAgentRunResponse,
  GrowthAgentSegmentCode,
  GrowthAgentSegmentMode,
  GrowthAgentWorkflow,
  GrowthAgentWorkflowId,
} from '../types';
import { useApp } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GenieAnswer } from '../components/mortgage/GenieAnswer';
import { GenieProgress } from '../components/mortgage/GenieProgress';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { descriptorFor, drawerForAsset } from '../lib/drawerSources';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  clearGenieConversationState,
  readGenieConversationId,
  writeGenieConversationId,
} from '../lib/genieConversation';
import { isGenieFollowUpQuestion } from '../lib/genieSession';
import { queryKeys } from '../lib/queryKeys';
import { GrowthAgentCapabilityPanel } from './ask-genie.growth-agent-capabilities';
import { GrowthAgentDraftPanel } from './ask-genie.growth-agent-drafts';
import { GrowthAgentRunCard, formatGrowthAgentCount } from './ask-genie.growth-run-card';
import { SavedGrowthAgentMonitors } from './ask-genie.saved-monitors';
import {
  CUSTOM_SEGMENTS,
  buildTrustedAssetQuestion,
  parseGrowthAgentStateInput,
  renderSourceAssetChip,
  trustedAssetsForCatalog,
  workflowIcon,
} from './ask-genie.growth-agent.helpers';

export { formatGrowthAgentCount };
export {
  buildTrustedAssetQuestion,
  parseGrowthAgentStateInput,
  trustedAssetsForCatalog,
} from './ask-genie.growth-agent.helpers';

const NON_PERSISTABLE_SOURCES = new Set([
  'degraded',
  'policy_blocked',
  'refused',
  'data_gap',
  'out_of_footprint',
]);

function shouldPersistConversation(payload: GenieAnswerShape): boolean {
  return Boolean(payload.conversation_id && !NON_PERSISTABLE_SOURCES.has(String(payload.source ?? '')));
}

export default function AskGenie() {
  const navigate = useNavigate();
  const { refreshWorkspace, setDrawer } = useApp();
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState('');
  const [sampleQuestions, setSampleQuestions] = useState<string[]>([]);
  const [activeAssetPath, setActiveAssetPath] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(() => readGenieConversationId());
  const [agentStateText, setAgentStateText] = useState('');
  const [agentCadence, setAgentCadence] = useState<GrowthAgentCadence>('daily');
  const [agentPrompt, setAgentPrompt] = useState(
    'Find prime refinance and listed-for-sale opportunities across current coverage.',
  );
  const [promptAgentPending, setPromptAgentPending] = useState(false);
  const [promptAgentPendingAction, setPromptAgentPendingAction] = useState<'run' | 'save' | null>(null);
  const [growthAgentPending, setGrowthAgentPending] = useState<GrowthAgentWorkflowId | null>(null);
  const [monitorPending, setMonitorPending] = useState<string | null>(null);
  const [monitorDraftPending, setMonitorDraftPending] = useState<string | null>(null);
  const [customAgentPendingAction, setCustomAgentPendingAction] = useState<'run' | 'save' | null>(null);
  const [growthAgentError, setGrowthAgentError] = useState<string | null>(null);
  const [latestGrowthRun, setLatestGrowthRun] = useState<GrowthAgentRunResponse | null>(null);
  const [latestGrowthDrafts, setLatestGrowthDrafts] = useState<GrowthAgentNotificationDraft[]>([]);
  const [customSegments, setCustomSegments] = useState<GrowthAgentSegmentCode[]>(['itm', 'listed']);
  const [customMode, setCustomMode] = useState<GrowthAgentSegmentMode>('any');

  function clearGrowthAgentFeedback() {
    setLatestGrowthRun(null);
    setLatestGrowthDrafts([]);
    setGrowthAgentError(null);
  }

  const growthAgentQuery = useQuery({
    queryKey: queryKeys.growthAgent(),
    queryFn: ({ signal }) => api.growthAgent(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const genieStartQuery = useQuery({
    queryKey: queryKeys.genieStart(),
    queryFn: ({ signal }) => api.genieStart(signal),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    const result = genieStartQuery.data;
    if (!result) return;
    setSampleQuestions(Array.isArray(result.sample_questions) ? result.sample_questions : []);
    if (conversationId) return;
    if (!result.conversation_id) return;
    setConversationId(result.conversation_id);
    writeGenieConversationId(result.conversation_id);
  }, [conversationId, genieStartQuery.data]);
  const trustedAssets = trustedAssetsForCatalog(genieStartQuery.data?.trusted_assets);
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  // `submittedQuestion` drives the warming-up-wrapped fetch. Typing in
  // the textarea updates `question`; clicking Ask commits the current
  // value into `submittedQuestion`, which triggers the hook. Pairing
  // with `submitToken` lets the same question be re-asked without
  // the hook no-op'ing on unchanged deps.
  const [submittedQuestion, setSubmittedQuestion] = useState<string | null>(null);
  const [submittedConversationId, setSubmittedConversationId] = useState<string | null>(null);
  const [submitToken, setSubmitToken] = useState<number>(0);

  const {
    data: payload,
    warmingUp,
    error,
    manualRetry,
  } = useWarmingUpRetry<GenieAnswerShape>(
    (signal) => api.genie(submittedQuestion ?? '', submittedConversationId, signal) as Promise<GenieAnswerShape>,
    [submittedQuestion, submittedConversationId, submitToken],
    {
      enabled: submittedQuestion !== null && submittedQuestion.length > 0,
      queryKey: queryKeys.genieAnswer([
        submittedQuestion ?? '',
        submittedConversationId ?? '',
        submitToken,
      ]),
      staleTime: Infinity,
      refetchOnWindowFocus: false,
    },
  );

  const loading = submittedQuestion !== null && payload === null && warmingUp === null && error === null;
  const errorMsg = error
    ? error instanceof Error
      ? `Couldn't reach Genie: ${error.message}`
      : "Couldn't reach Genie."
    : null;

  useEffect(() => {
    if (!(error instanceof ApiError) || error.status !== 403) return;
    setConversationId(null);
    setSubmittedConversationId(null);
    clearGenieConversationState();
  }, [error]);

  useEffect(() => {
    const onActorBoundaryReset = () => {
      setConversationId(null);
      setSubmittedConversationId(null);
      setSubmittedQuestion(null);
      setQuestion('');
      setActiveAssetPath(null);
      setActionStatus(null);
    };
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    return () => {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    };
  }, []);

  function ask(q: string) {
    const trimmed = q.trim();
    const nextConversationId = isGenieFollowUpQuestion(trimmed) ? conversationId : null;
    setQuestion(q);
    setConversationId(nextConversationId);
    setSubmittedConversationId(nextConversationId);
    setSubmittedQuestion(trimmed);
    setSubmitToken((n) => n + 1);
    setActiveAssetPath(null);
    setActionStatus(null);
    if (!nextConversationId) {
      clearGenieConversationState();
    }
  }

  function newConversation() {
    setConversationId(null);
    setSubmittedConversationId(null);
    setSubmittedQuestion(null);
    setActiveAssetPath(null);
    setActionStatus('Started a new Genie thread.');
    clearGenieConversationState();
  }

  function scopeToTrustedAsset(asset: { label: string; path: string }) {
    const scopedQuestion = buildTrustedAssetQuestion(asset);
    setDrawer(descriptorFor(asset.path));
    setQuestion(scopedQuestion);
    setActiveAssetPath(asset.path);
    questionRef.current?.focus();
  }

  async function runGrowthAgentWorkflow(workflow: GrowthAgentWorkflow, saveMonitor: boolean) {
    const parsed = parseGrowthAgentStateInput(agentStateText);
    if (parsed.invalid.length > 0) {
      setLatestGrowthRun(null);
      setGrowthAgentError(`Use two-letter state codes only: ${parsed.invalid.join(', ')}`);
      return;
    }
    setGrowthAgentPending(workflow.id);
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
    }
  }

  async function runMortgageGrowthAgentPrompt(saveMonitor: boolean) {
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
    }
  }

  async function runCustomGrowthAgentWorkflow(saveMonitor: boolean) {
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
    }
  }

  async function rerunGrowthAgentMonitor(monitor: GrowthAgentMonitor) {
    setMonitorPending(monitor.monitor_id);
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
    }
  }

  async function draftGrowthAgentMonitorNotifications(monitor: GrowthAgentMonitor) {
    setMonitorDraftPending(monitor.monitor_id);
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

  useEffect(() => {
    if (!payload?.conversation_id || !shouldPersistConversation(payload)) return;
    const nextConversationId = payload.conversation_id;
    setConversationId(nextConversationId);
    writeGenieConversationId(nextConversationId);
  }, [payload]);

  async function runAction(action: GenieActionSuggestion) {
    setActionStatus(`Running ${action.label.toLowerCase()}...`);
    try {
      const result = await api.genieAction({
        ...action,
        conversation_id: payload?.conversation_id ?? conversationId,
        message_id: payload?.message_id ?? null,
        question_hash: payload?.question_hash ?? null,
      });
      if (!result.ok) {
        setActionStatus(`Action failed: ${result.message}`);
        return;
      }
      setActionStatus(
        result.audit_event_id
          ? `${result.message} Audit event ${result.audit_event_id}.`
          : result.message,
      );
      if (action.action_type === 'save_borrowers') refreshWorkspace();
      if (result.route) navigate(result.route);
    } catch (err) {
      setActionStatus(
        err instanceof Error
          ? `Action failed: ${err.message}`
          : 'Action failed.',
      );
    }
  }

  const sourceLabel = payload?.source ?? '';
  // The backend emits "genie" for live answers, or governed refusal/degraded
  // source values when it intentionally stops before showing data.
  const isDegraded = sourceLabel === 'degraded';
  const isBlocked =
    sourceLabel === 'policy_blocked' ||
    sourceLabel === 'refused' ||
    sourceLabel === 'data_gap' ||
    sourceLabel === 'out_of_footprint';
  const sourceChip = isDegraded
    ? 'Genie reconnecting'
    : isBlocked
      ? sourceLabel === 'refused'
        ? 'Prompt refused'
        : sourceLabel === 'data_gap'
          ? 'Source pending'
          : sourceLabel === 'out_of_footprint'
            ? 'Outside footprint'
          : 'Policy blocked'
      : payload?.trusted_assets?.[0] || sourceLabel || '';
  const sourceChipTitle = isDegraded
    ? 'The Genie answer path is temporarily unavailable. Live answers will resume after health recovers.'
    : isBlocked
      ? 'The answer was not displayed because it did not meet the governed Genie policy.'
    : undefined;
  const sourceChipVariant: 'warning' | undefined = isDegraded || isBlocked ? 'warning' : undefined;
  const drawerForSource = sourceChip ? drawerForAsset(sourceChip) : null;
  const composerSampleQuestions = sampleQuestions.slice(0, 4);
  const stateParsePreview = parseGrowthAgentStateInput(agentStateText);
  const workflows = growthAgentQuery.data?.workflows ?? [];
  const monitors = growthAgentQuery.data?.monitors ?? [];
  const agentBusy = growthAgentPending !== null || promptAgentPending || monitorPending !== null || monitorDraftPending !== null;
  const capabilityRows = (growthAgentQuery.data?.capabilities ?? []).filter((row) => (
    [
      'genie_conversation_api',
      'certified_metric_views',
      'uc_function_tools',
      'agent_orchestrator',
      'ai_gateway',
      'lakebase_sync',
      'agent_eval',
    ].includes(row.key) && row.status !== 'hidden'
  ));

  return (
    <PageShell
      eyebrow="Mortgage Growth Agent"
      title="Governed mortgage growth co-pilot"
      lede="Route borrower-growth objectives to reviewed SQL workflows, review the exact eligible Lead Queue subset, then ask Genie follow-up questions against trusted Unity Catalog assets."
      heroRight={<Chip variant="neutral" icon="sparkle">Databricks Genie + SQL</Chip>}
    >
      <div className="surface growth-agent" aria-busy={agentBusy}>
        <div className="surface__hdr">
          <Icon name="bolt" size={14} className="icon-accent" />
          <div>
            <div className="h-4">Mortgage Growth Agent</div>
            <div className="muted fs-12">Reviewed workflow runs, saved watchlists, and human-review Lead Queue handoffs.</div>
          </div>
          <div className="spacer" />
          <Chip variant="success" icon="shield">Draft-only handoffs · no auto-send</Chip>
          <Chip variant="neutral" icon="audit">Audit checked after each run</Chip>
        </div>
        <div className="surface__body">
          <section className="growth-agent-capabilities" aria-label="Growth Agent capability boundaries">
            <GrowthAgentCapabilityPanel
              rows={capabilityRows}
              isPending={growthAgentQuery.isPending}
            />
          </section>
          <section className="growth-agent-command" aria-label="Mortgage Growth Agent command center">
            <div className="growth-agent-command__main">
              <label className="growth-agent__field">
                <span>Growth objective</span>
                <textarea
                  className="route-textarea growth-agent-command__prompt"
                  aria-label="Mortgage Growth Agent prompt"
                  value={agentPrompt}
                  onChange={(event) => {
                    setAgentPrompt(event.target.value);
                    clearGrowthAgentFeedback();
                  }}
                />
                <span className="growth-agent__hint">
                  The co-pilot selects reviewed workflows only; raw borrower identifiers, PII, protected-class targeting, and unreviewed source requests are rejected.
                </span>
              </label>
            </div>
            <div className="growth-agent-command__actions">
              <Button
                variant="primary"
                size="sm"
                icon="sparkle"
                onClick={() => runMortgageGrowthAgentPrompt(false)}
                disabled={agentBusy || stateParsePreview.invalid.length > 0}
              >
                {promptAgentPending && promptAgentPendingAction === 'run' ? 'Planning…' : 'Plan reviewed workflow'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon="bell"
                onClick={() => runMortgageGrowthAgentPrompt(true)}
                disabled={agentBusy || stateParsePreview.invalid.length > 0}
              >
                {promptAgentPending && promptAgentPendingAction === 'save' ? 'Saving…' : 'Save reviewed watchlist'}
              </Button>
            </div>
          </section>

          <div className="growth-agent__controls">
            <label className="growth-agent__field">
              <span>State scope</span>
              <input
                className="form-input"
                aria-label="Growth Agent state scope"
                placeholder="All states or IL, TX, CA"
                value={agentStateText}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  setAgentStateText(nextValue);
                  clearGrowthAgentFeedback();
                }}
              />
              <span className="growth-agent__hint">
                {stateParsePreview.invalid.length > 0
                  ? `Invalid: ${stateParsePreview.invalid.join(', ')}`
                  : stateParsePreview.states.length > 0
                    ? `Scoped to ${stateParsePreview.states.join(', ')}`
                    : 'No state scope; the workflow uses the current coverage footprint.'}
              </span>
            </label>
            <label className="growth-agent__field growth-agent__field--compact">
                  <span>Review interval</span>
                  <select
                    className="form-input"
                    aria-label="Growth Agent review interval"
                    value={agentCadence}
                onChange={(event) => {
                  setAgentCadence(event.target.value as GrowthAgentCadence);
                  clearGrowthAgentFeedback();
                }}
              >
                    <option value="daily">Daily interval</option>
                    <option value="weekly">Weekly interval</option>
                  </select>
                  <span className="growth-agent__hint">Saving stores reviewed filters. Automatic refresh is paused until an admin enables the draft-only scheduler.</span>
            </label>
          </div>

          {growthAgentError && (
            <div className="status-callout status-callout--danger mt-3" role="alert">
              {growthAgentError}
            </div>
          )}
          {growthAgentQuery.error && (
            <div className="status-callout status-callout--danger mt-3" role="alert">
              Could not load Growth Agent workflows.
            </div>
          )}

          <div className="growth-agent__cards" aria-label="Governed Growth Agent workflows">
            {workflows.map((workflow) => {
              const pending = growthAgentPending === workflow.id;
              return (
                <article key={workflow.id} className="growth-agent-card">
                  <div className="growth-agent-card__head">
                    <span className="growth-agent-card__icon">
                      <Icon name={workflowIcon(workflow.id)} size={16} />
                    </span>
                    <div>
                      <div className="growth-agent-card__title">{workflow.title}</div>
                      <div className="growth-agent-card__trigger">{workflow.trigger_label}</div>
                    </div>
                  </div>
                  <p className="growth-agent-card__copy">{workflow.objective}</p>
                  <div className="growth-agent-card__proof">
                    {workflow.proof_points.slice(0, 3).map((point) => (
                      <div key={point} className="growth-agent-card__proof-line">
                        <Icon name="check" size={11} />
                        <span>{point}</span>
                      </div>
                    ))}
                  </div>
                  <div className="chip-row growth-agent-card__assets">
                    {workflow.source_assets.slice(0, 3).map((asset) => renderSourceAssetChip(asset))}
                  </div>
                  <div className="growth-agent-card__actions">
                    <Button
                      variant="primary"
                      size="sm"
                      icon="play"
                      onClick={() => runGrowthAgentWorkflow(workflow, false)}
                      disabled={agentBusy || stateParsePreview.invalid.length > 0}
                    >
                      {pending ? 'Running…' : 'Run'}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="bell"
                      onClick={() => runGrowthAgentWorkflow(workflow, true)}
                      disabled={agentBusy || stateParsePreview.invalid.length > 0}
                    >
                      Save watchlist
                    </Button>
                  </div>
                </article>
              );
            })}
            {growthAgentQuery.isPending && (
              <div className="surface surface--inset growth-agent-card growth-agent-card--loading">
                <div className="surface__body">Loading governed workflows…</div>
              </div>
            )}
          </div>

          <div className="growth-agent-custom" aria-label="Build a custom Growth Agent workflow">
            <div className="growth-agent-custom__head">
              <Icon name="filter" size={14} className="icon-accent" />
              <div>
                <div className="h-4">Build a custom segment workflow</div>
                <div className="muted fs-12">
                  Combine reviewed Module 0 segment signals, reconcile to eligible leads, then save the watchlist filters.
                </div>
              </div>
              <div className="spacer" />
              <Chip variant="neutral" icon="shield">Reviewed vocabulary only</Chip>
            </div>
            <div className="growth-agent-custom__body">
              <div className="chip-row" role="group" aria-label="Custom workflow segments">
                {CUSTOM_SEGMENTS.map((segment) => {
                  const selected = customSegments.includes(segment.code);
                  return (
                    <button
                      key={segment.code}
                      type="button"
                      className={`filter ${selected ? 'is-active' : ''}`}
                      aria-pressed={selected}
                      onClick={() => toggleCustomSegment(segment.code)}
                    >
                      <span className="filter__value">{segment.label}</span>
                    </button>
                  );
                })}
              </div>
              <label className="growth-agent__field growth-agent__field--compact">
                <span>Segment logic</span>
                <select
                  className="form-input"
                  aria-label="Custom Growth Agent segment logic"
                  value={customMode}
                  onChange={(event) => {
                    setCustomMode(event.target.value as GrowthAgentSegmentMode);
                    clearGrowthAgentFeedback();
                  }}
                >
                  <option value="any">Any selected segment</option>
                  <option value="all">All selected segments</option>
                </select>
                <span className="growth-agent__hint">
                  Any de-duplicates borrowers across selected segments; All requires every selected segment.
                </span>
              </label>
              <div className="growth-agent-card__actions">
                <Button
                  variant="primary"
                  size="sm"
                  icon="play"
                  onClick={() => runCustomGrowthAgentWorkflow(false)}
                  disabled={agentBusy || customSegments.length === 0 || stateParsePreview.invalid.length > 0}
                >
                  {growthAgentPending === 'custom_segment_watch' && customAgentPendingAction === 'run' ? 'Running…' : 'Run custom'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  icon="bell"
                  onClick={() => runCustomGrowthAgentWorkflow(true)}
                  disabled={agentBusy || customSegments.length === 0 || stateParsePreview.invalid.length > 0}
                >
                  {growthAgentPending === 'custom_segment_watch' && customAgentPendingAction === 'save' ? 'Saving…' : 'Save custom watchlist'}
                </Button>
              </div>
            </div>
          </div>

          {latestGrowthRun && (
            <GrowthAgentRunCard
              run={latestGrowthRun}
              onOpenRoute={(route) => navigate(route)}
              renderSourceAssetChip={renderSourceAssetChip}
            />
          )}

          <GrowthAgentDraftPanel drafts={latestGrowthDrafts} />

          <SavedGrowthAgentMonitors
            monitors={monitors}
            monitorPending={monitorPending}
            draftPending={monitorDraftPending}
            actionsDisabled={agentBusy}
            onRun={rerunGrowthAgentMonitor}
            onDraft={draftGrowthAgentMonitorNotifications}
            onOpen={(route) => navigate(route)}
          />
        </div>
      </div>

      <div className="layoutA-grid">
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="sparkle" size={14} className="icon-accent" />
            <div className="h-4">Ask a question</div>
          </div>
          <div className="surface__body">
            <textarea
              ref={questionRef}
              aria-label="Ask Genie — question"
              value={question}
              onChange={(e) => {
                setQuestion(e.target.value);
                setActiveAssetPath(null);
              }}
              onKeyDown={(e) => {
                // 2026-05-04 (FIX Δ1): standard chat keymap — Enter
                // submits, Shift+Enter inserts a newline. Match how
                // Slack / GitHub PRs behave so the keyboard-first user
                // doesn't have to mouse over to the Ask Genie button.
                // The submit-disabled guard mirrors the button's
                // `disabled` prop so a stray Enter during a warming-up
                // request can't double-fire.
                if (
                  e.key === 'Enter' &&
                  !e.shiftKey &&
                  !e.metaKey &&
                  !e.ctrlKey &&
                  !e.altKey
                ) {
                  e.preventDefault();
                  if (!loading && warmingUp === null && question.trim().length > 0) {
                    ask(question);
                  }
                }
              }}
              className="route-textarea route-textarea--genie"
            />
            {composerSampleQuestions.length > 0 && (
              <div className="genie-composer__samples" aria-label="Suggested Genie questions">
                {composerSampleQuestions.map((q) => (
                  <button
                    key={q}
                    type="button"
                    className="filter filter--question"
                    onClick={() => ask(q)}
                  >
                    <Icon name="sparkle" size={11} />
                    <span className="filter__text">{q}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="section-actions">
              <Button
                variant="primary"
                icon="send"
                onClick={() => ask(question)}
                disabled={loading || warmingUp !== null || question.trim().length === 0}
              >
                {loading || warmingUp !== null ? 'Asking…' : 'Ask Genie'}
              </Button>
              <Button
                variant="ghost"
                icon="chat"
                onClick={newConversation}
                disabled={loading || warmingUp !== null}
              >
                New thread
              </Button>
            </div>
            {warmingUp && (
              <div className="mt-4">
                <WarmingUpBlock state={warmingUp} title="Asking Genie" compact />
              </div>
            )}
            {loading && !warmingUp && (
              <div className="surface surface--inset mt-4">
                <div className="surface__body">
                  <GenieProgress />
                </div>
              </div>
            )}
            {errorMsg && !warmingUp && (
              <div
                className="surface surface--inset surface--danger mt-4"
                role="alert"
              >
                <div className="surface__body status-callout--danger">
                  <span>{errorMsg}</span>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={manualRetry}
                    disabled={loading}
                    aria-label="Retry Genie question"
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}
            {!payload && !loading && !warmingUp && !errorMsg && (
              <div className="surface surface--inset mt-4">
                <div className="surface__body genie-empty">
                  <div className="genie-empty__icon">
                    <Icon name="sparkle" size={16} />
                  </div>
                  <div>
                    <div className="genie-empty__title">Ready for governed analysis</div>
                    <p className="genie-empty__copy">
                      Trusted SQL, source assets, freshness, and approval-safe actions appear with each answer.
                    </p>
                  </div>
                </div>
              </div>
            )}
            {payload && (
              <div
                className="surface surface--inset mt-4"
              >
                <div className="surface__body">
                  {sourceChip && (
                    <div className="chip-row mb-3">
                      <span className="muted fs-11">Source:</span>
                      {sourceChipVariant === 'warning' ? (
                        // Degraded: warning chip with tooltip so the user
                        // knows Genie is reconnecting. Not clickable.
                        <Chip
                          variant="warning"
                          icon="info"
                          title={sourceChipTitle}
                        >
                          {sourceChip}
                        </Chip>
                      ) : drawerForSource ? (
                        // Specific UC asset → open the matching drawer entry.
                        <EvidenceChip source={drawerForSource}>{sourceChip}</EvidenceChip>
                      ) : (
                        // Generic / unknown source → inert chip so a click
                        // doesn't open the wrong drawer. (Prior code
                        // defaulted to NBO and was misleading.)
                        <Chip variant="neutral" title={`Source: ${sourceChip}`}>
                          {sourceChip}
                        </Chip>
                      )}
                    </div>
                  )}
                  {/* withChart=true: opt this deep-dive view in to the
                      auto-detected bar chart for top-N / per-state-style
                      table_rows payloads. The floating bubble does NOT
                      pass this prop, so its compact form is unchanged. */}
                  <GenieAnswer payload={payload} question={submittedQuestion ?? undefined} onFollowUp={ask} onAction={runAction} withChart />
                  {actionStatus && (
                    <div className="status-callout status-callout--info mt-3">
                      {actionStatus}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="stack-grid">
          <div className="surface">
            <div className="surface__hdr">
              <Icon name="layers" size={14} className="icon-accent" />
              <div className="h-4">Trusted assets</div>
            </div>
            <div className="surface__body trusted-asset-list">
              {trustedAssets.map((a) => (
                <button
                  key={a.path}
                  title={a.path}
                  type="button"
                  className={`trusted-asset trusted-asset--button${activeAssetPath === a.path ? ' is-active' : ''}`}
                  onClick={() => scopeToTrustedAsset(a)}
                  aria-pressed={activeAssetPath === a.path}
                >
                  <div className="trusted-asset__label">{a.label}</div>
                  <div className="trusted-asset__path">{a.path}</div>
                </button>
              ))}
            </div>
          </div>

        </div>
      </div>
    </PageShell>
  );
}
