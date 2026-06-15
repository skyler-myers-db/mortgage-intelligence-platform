import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ApiError, api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../types';
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

/**
 * Ask Genie — deep-dive view with trusted-asset list and backend-provided
 * prompt suggestions.
 * The floating GenieChat in the AppShell is the "ask anywhere" entry point;
 * this route is the curated walkthrough for stakeholders who want to see
 * which UC metric views Genie is grounded on. Answer rendering is delegated
 * to the shared <GenieAnswer> so metric_value / table_rows / follow_ups
 * surface identically here and in the floating chat.
 */

// Friendly name + catalog-relative suffix. The backend returns the concrete
// catalog-qualified paths for this deployment on /api/genie/start.
const TRUSTED_ASSET_DEFS: Array<{ label: string; suffix: string }> = [
  { label: 'Borrower population',          suffix: 'gold.lead_population' },
  { label: 'Segment rollups',              suffix: 'gold.segment_population' },
  { label: 'Opportunity scores',           suffix: 'gold.lead_scores' },
  { label: 'Borrower 360 profile',         suffix: 'gold.borrower_360' },
  { label: 'Borrower dossier',             suffix: 'gold.borrower_dossier' },
  { label: 'Source evidence',              suffix: 'gold.evidence_events' },
  { label: 'Source readiness',             suffix: 'gold.source_readiness' },
  { label: 'Lock-in cohort',               suffix: 'gold.lockin_cohort' },
  { label: 'Lead-generation metric view',  suffix: 'semantics.lead_generation_metric_view' },
  { label: 'Segment performance view',     suffix: 'semantics.segment_performance_metric_view' },
  { label: 'Borrower opportunity view',    suffix: 'semantics.borrower_opportunity_metric_view' },
];

export function trustedAssetsForCatalog(
  startAssets: string[] | undefined,
): Array<{ label: string; path: string }> {
  return TRUSTED_ASSET_DEFS.map((asset) => ({
    label: asset.label,
    path: startAssets?.find((path) => path.endsWith(`.${asset.suffix}`))
      ?? asset.suffix,
  }));
}

export function buildTrustedAssetQuestion(asset: { label: string; path: string }): string {
  return `Using ${asset.path}, summarize what this trusted asset can answer for Module 0 and show the most useful fields for ${asset.label.toLowerCase()}.`;
}

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
    ? 'The Genie space is warming up. Live answers will resume shortly.'
    : isBlocked
      ? 'The answer was not displayed because it did not meet the governed Genie policy.'
    : undefined;
  const sourceChipVariant: 'warning' | undefined = isDegraded || isBlocked ? 'warning' : undefined;
  // Map the Genie-provided source label to the best matching drawer entry.
  // Returns null when no specific match exists — the chip then renders as
  // an inert neutral chip rather than defaulting to NBO and misleading
  // the user into the wrong drawer (the prior "default to NBO" routing
  // was confusing per 2026-05-04 user feedback). Governed refusal/degraded
  // chips also render inert (the chip text is the explanation).
  const drawerForSource = sourceChip ? drawerForAsset(sourceChip) : null;
  const composerSampleQuestions = sampleQuestions.slice(0, 4);

  return (
    <PageShell
      eyebrow="Ask Genie"
      title="Ask Genie about segments, borrowers, and triggers"
      lede="Type a question or pick a suggestion. Answers cite the metric view that produced them; tap a source chip to open lineage."
      heroRight={<Chip variant="neutral" icon="sparkle">Databricks Genie API</Chip>}
    >
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
                  {sourceChip && (
                    <div className="chip-row mt-3">
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
