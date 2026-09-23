import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { ApiError, api, type GenieLiveProgress } from '../lib/api';
import { GenieLiveError, askGenieLive } from '../lib/genieAsk';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type {
  GenieActionSuggestion,
  GenieAnswer as GenieAnswerShape,
} from '../types';
import { useApp } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { descriptorFor } from '../lib/drawerSources';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  clearGenieConversationState,
  readGenieConversationId,
  writeGenieConversationId,
} from '../lib/genieConversation';
import {
  clearGenieTurns,
  setGenieTurns,
  type GenieTurn,
} from '../lib/genieConversationStore';
import { queryKeys } from '../lib/queryKeys';
import { AskGenieAnswerPanel } from './ask-genie.answer-panel';
import { GrowthAgentPanel } from './ask-genie.growth-agent-panel';
import { useGrowthAgentWorkspace } from './ask-genie.growth-agent-state';
import { formatGrowthAgentCount } from './ask-genie.growth-run-card';
import {
  buildTrustedAssetQuestion,
  trustedAssetsForCatalog,
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
  const suppressBootstrapConversationRef = useRef(false);
  const [question, setQuestion] = useState('');
  const [sampleQuestions, setSampleQuestions] = useState<string[]>([]);
  const [activeAssetPath, setActiveAssetPath] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(() => readGenieConversationId());
  const growthAgent = useGrowthAgentWorkspace();

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
    const startConversationId = result.conversation_id;
    if (!startConversationId || suppressBootstrapConversationRef.current) return;
    setConversationId((current) => {
      if (current) return current;
      writeGenieConversationId(startConversationId);
      return startConversationId;
    });
  }, [genieStartQuery.data]);
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
  // Live lifecycle telemetry for the in-flight turn (stage rail, public
  // process steps, generated SQL) from the submit → progress → complete flow.
  const [liveProgress, setLiveProgress] = useState<GenieLiveProgress | null>(null);
  const [askStartedAt, setAskStartedAt] = useState<number | null>(null);
  // Generation counter: an aborted ask settles AFTER its replacement has
  // already started, and its cleanup must not clobber the new turn's
  // ticker/rail state (QA M2 — sample-question chips can re-ask mid-flight).
  const askGenerationRef = useRef(0);

  const {
    data: payload,
    warmingUp,
    error,
    manualRetry,
  } = useWarmingUpRetry<GenieAnswerShape>(
    (signal) => {
      const generation = ++askGenerationRef.current;
      setAskStartedAt(Date.now());
      setLiveProgress(null);
      return (
        askGenieLive(submittedQuestion ?? '', submittedConversationId, {
          signal,
          onProgress: (p) => {
            if (askGenerationRef.current === generation) setLiveProgress(p);
          },
        }) as Promise<GenieAnswerShape>
      ).finally(() => {
        if (askGenerationRef.current === generation) {
          setLiveProgress(null);
          setAskStartedAt(null);
        }
      });
    },
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
    ? error instanceof GenieLiveError
      ? error.message
      : error instanceof Error
        ? `Couldn't reach Genie: ${error.message}`
        : "Couldn't reach Genie."
    : null;

  useEffect(() => {
    if (!(error instanceof ApiError) || error.status !== 403) return;
    setConversationId(null);
    setSubmittedConversationId(null);
    clearGenieTurns();
    clearGenieConversationState({ notify: true });
  }, [error]);

  useEffect(() => {
    const onActorBoundaryReset = () => {
      suppressBootstrapConversationRef.current = true;
      setConversationId(null);
      setSubmittedConversationId(null);
      setSubmittedQuestion(null);
      setQuestion('');
      setActiveAssetPath(null);
      setActionStatus(null);
      // An actor-boundary reset invalidates the transcript too: the prior
      // actor's questions must not linger in this tab. Mirrors GenieChat.
      clearGenieTurns();
    };
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    return () => {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    };
  }, []);

  function ask(q: string, followUpConversationId?: string | null) {
    const trimmed = q.trim();
    const activeConversationId = followUpConversationId ?? conversationId;
    setQuestion(q);
    setConversationId(activeConversationId);
    setSubmittedConversationId(activeConversationId);
    setSubmittedQuestion(trimmed);
    setSubmitToken((n) => n + 1);
    setActiveAssetPath(null);
    setActionStatus(null);
    if (!activeConversationId) {
      clearGenieConversationState();
    }
  }

  function newConversation() {
    suppressBootstrapConversationRef.current = true;
    setConversationId(null);
    setSubmittedConversationId(null);
    setSubmittedQuestion(null);
    setActiveAssetPath(null);
    setQuestion('');
    clearGenieTurns();
    // Order matters: the reset event this dispatches is handled synchronously
    // by this route's own listener, which clears `actionStatus`. Setting the
    // confirmation BEFORE the dispatch left it permanently invisible.
    clearGenieConversationState({ notify: true });
    setActionStatus('Started a new Genie thread.');
  }

  /**
   * Restore a past conversation from the History menu. The loaded turns are
   * the same `{question, response}` shape the local store persists, so they
   * render through the identical <GenieAnswer> path. The conversation id is
   * adopted too, so a follow-up continues that Databricks thread rather than
   * opening an orphan one. Mirrors GenieChat.loadSession.
   */
  function loadSession(conversationIdToLoad: string, turns: GenieTurn[]) {
    suppressBootstrapConversationRef.current = true;
    setGenieTurns(turns);
    setConversationId(conversationIdToLoad);
    // Drop the in-memory answer so the settled turn of the PREVIOUS thread
    // cannot re-append itself onto the restored one.
    setSubmittedConversationId(null);
    setSubmittedQuestion(null);
    setQuestion('');
    setActiveAssetPath(null);
    setActionStatus(null);
    writeGenieConversationId(conversationIdToLoad);
  }

  /** A turn settled: the composer must not keep the question it answered.
   *  Left alone when the user has already typed the next one. */
  function clearAnsweredQuestion(asked: string) {
    setQuestion((current) => (current.trim() === asked.trim() ? '' : current));
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

  return (
    <PageShell
      eyebrow="Mortgage Growth Agent"
      title="Mortgage growth co-pilot"
      lede="Use the Genie Conversation API for portfolio analysis, then compose and run reviewed growth workflows with human approval at the action boundary. Databricks Agent Responses automation is identified only when the configured capability is ready."
      heroRight={<Chip variant="neutral" icon="sparkle">Genie analytics + reviewed automation</Chip>}
    >
      <GrowthAgentPanel agent={growthAgent} onOpenRoute={(route) => navigate(route)} />

      <div className="layoutA-grid">
        <AskGenieAnswerPanel
          questionRef={questionRef}
          question={question}
          onQuestionChange={(value) => {
            setQuestion(value);
            setActiveAssetPath(null);
          }}
          onAsk={ask}
          onNewThread={newConversation}
          onLoadSession={loadSession}
          onSettled={clearAnsweredQuestion}
          loading={loading}
          warmingUp={warmingUp}
          errorMsg={errorMsg}
          onRetry={manualRetry}
          sampleQuestions={sampleQuestions}
          payload={payload}
          liveProgress={liveProgress}
          askStartedAt={askStartedAt}
          submittedQuestion={submittedQuestion}
          onFollowUp={ask}
          onAction={runAction}
          onEditQuestion={(q) => {
            setQuestion(q);
            setActiveAssetPath(null);
            questionRef.current?.focus();
          }}
          actionStatus={actionStatus}
        />

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
                </button>
              ))}
            </div>
          </div>

        </div>
      </div>
    </PageShell>
  );
}
