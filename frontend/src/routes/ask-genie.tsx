import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';
import type {
  GenieActionSuggestion,
  GenieAnswer as GenieAnswerShape,
} from '../types';
import { useApp } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Icon } from '../components/Icon';
import { genieActionConfirmation, runGenieActionRequest } from '../components/mortgage/GenieChat.helpers';
import { GenieAnnouncerRegion } from '../components/mortgage/GenieAnnouncerRegion';
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
import {
  announceGenie,
  clearGenieTurnNotes,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
  startGenieTurn,
  subscribeGenieTurnSettled,
} from '../lib/genieInFlightTurn';
import { ensureGenieLauncherSignal } from '../lib/genieLauncherSignal';
import { genieStartQueryOptions } from '../lib/genieStartQuery';
import { AskGenieAnswerPanel } from './ask-genie.answer-panel';
import {
  GenieConversationLinkPlaceholder,
  NO_GENIE_TURN,
  genieConversationQueryKey,
  isGenieConversationId,
  useGenieConversationLink,
  useInFlightGenieConversation,
  type GenieConversationLinkOk,
} from './ask-genie.deep-link';
import { GrowthAgentMonitorsPanel } from './ask-genie.growth-agent-monitors';
import { GrowthAgentPanel } from './ask-genie.growth-agent-panel';
import { useGrowthAgentWorkspace } from './ask-genie.growth-agent-state';
import {
  AskGenieTabs,
  askGeniePanelId,
  askGenieTabId,
  useAskGenieTab,
} from './ask-genie.tabs';
import { formatGrowthAgentCount } from './ask-genie.growth-run-card';
import {
  buildTrustedAssetQuestion,
  trustedAssetsForCatalog,
} from './ask-genie.growth-agent.helpers';
import './ask-genie.css';

export { formatGrowthAgentCount };
export {
  buildTrustedAssetQuestion,
  parseGrowthAgentStateInput,
  trustedAssetsForCatalog,
} from './ask-genie.growth-agent.helpers';

/**
 * `/ask-genie` — conversation first (audit 2026-09-21 `visual-07`, `genie-09`,
 * `flow-10`). The page is titled what the nav calls it and opens on the Ask
 * tab: the thread, the suggestions and the docked composer. The Mortgage
 * Growth Agent's workflows and saved monitors are the Workflows and Saved
 * monitors tabs, selected by `?tab=` so they deep-link and Back works.
 *
 * The turn is the tab's, not the route's (wave 2 `runtime-01`): asking starts
 * it in lib/genieInFlightTurn, which settles it into the shared thread whether
 * or not this route is still mounted. Leaving the page does not stop it;
 * coming back shows the same pending turn, or its answer. A reload resumes a
 * turn that was still polling (the first Genie surface to mount asks).
 *
 * The route's ONE screen-reader announcer (`a11y-06`) is a direct child of the
 * page, outside every tabpanel: a hidden tabpanel is not spoken, and a turn
 * can land while the Workflows tab shows. It speaks only while the floating
 * panel is closed (useGenieAnnouncer picks one speaker).
 *
 * `/ask-genie/:conversationId` (audit `shell-03`, ask-genie.deep-link.tsx)
 * renders this same page: until the link's conversation is verified as the
 * actor's own, the Ask tab shows its loading / not-found / unavailable state
 * in place of the thread and the composer, and nothing is written. History
 * load puts a shape-valid id in the URL; a reset (actor change, New thread, a
 * fail-closed 403) takes it out. A settled turn never rewrites the URL.
 */
/** Adopt a conversation into the shared thread: its turns, and its id for
 *  follow-ups. The route's own id state is set by the caller. */
function adoptGenieConversation(conversation: GenieConversationLinkOk): void {
  setGenieTurns(conversation.turns);
  clearGenieTurnNotes();
  writeGenieConversationId(conversation.conversationId);
}

export default function AskGenie() {
  const navigate = useNavigate();
  const [tab, selectTab] = useAskGenieTab();
  const { refreshWorkspace, setDrawer } = useApp();
  const queryClient = useQueryClient();
  const questionRef = useRef<HTMLTextAreaElement>(null);
  // A conversation deep link (audit shell-03): a shape-valid URL id wins over
  // the stored id, so the genieStart bootstrap never overrides it.
  const { conversationId: conversationParam } = useParams<{ conversationId?: string }>();
  const { search, hash } = useLocation();
  const link = useGenieConversationLink(conversationParam);
  const busyConversation = useInFlightGenieConversation();
  const suppressBootstrapConversationRef = useRef(conversationParam !== undefined);
  /** The linked conversation last adopted into the thread. */
  const hydratedIdRef = useRef<string | null>(null);
  /** The URL as last rendered, for the reset listener. */
  const urlRef = useRef({ conversationParam, search });
  const focusComposerRef = useRef(false);
  const [question, setQuestion] = useState('');
  const [sampleQuestions, setSampleQuestions] = useState<string[]>([]);
  const [activeAssetPath, setActiveAssetPath] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(() => readGenieConversationId());
  const growthAgent = useGrowthAgentWorkspace();

  // Shared with the floating panel (lib/genieStartQuery, audit `runtime-06`).
  const genieStartQuery = useQuery(genieStartQueryOptions());

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

  // The launchers ring for a turn asked here even before the floating panel
  // first mounts (Genie residual #2), and a reload may have interrupted a
  // turn: resume it, once per page.
  useEffect(() => {
    ensureGenieLauncherSignal();
    resumeGenieTurnFromSession();
  }, []);

  // The linked conversation, once verified, is adopted after the URL commit
  // (a layout effect, so the previous thread is never painted under the new
  // URL; the shell's scroll reset runs next and the passive latest-exchange
  // reveal after it). A turn in flight in ANOTHER conversation holds it: the
  // current thread and its Stop stay, and it opens once that turn has landed.
  const linked = link.kind === 'ok' ? link.conversation : null;
  const held = linked !== null && busyConversation !== NO_GENIE_TURN && busyConversation !== linked.conversationId;
  useLayoutEffect(() => {
    urlRef.current = { conversationParam, search };
    if (conversationParam === undefined) hydratedIdRef.current = null;
    if (!linked || held || hydratedIdRef.current === linked.conversationId) return;
    hydratedIdRef.current = linked.conversationId;
    suppressBootstrapConversationRef.current = true;
    adoptGenieConversation(linked);
    setConversationId(linked.conversationId);
  }, [conversationParam, search, linked, held]);

  // Leaving the link (Open Ask Genie on a not-found link) hands focus to the
  // composer that replaces the button.
  useEffect(() => {
    if (link.kind !== 'none' || !focusComposerRef.current) return;
    focusComposerRef.current = false;
    questionRef.current?.focus();
  }, [link.kind]);

  // A turn settled, from either surface: follow the conversation the store
  // persisted. A turn asked HERE also clears the composer, but only while it
  // still holds the question that was answered (a new draft is kept).
  useEffect(
    () =>
      subscribeGenieTurnSettled((event) => {
        if (event.persistedConversationId) setConversationId(event.persistedConversationId);
        if (event.surface !== 'route') return;
        setQuestion((current) => (current.trim() === event.question.trim() ? '' : current));
      }),
    [],
  );

  useEffect(() => {
    // The turn store aborts the turn itself on this event; this clears what
    // the route holds. The prior actor's questions must not linger.
    const onActorBoundaryReset = () => {
      suppressBootstrapConversationRef.current = true;
      hydratedIdRef.current = null;
      // Actor change, New thread on either surface, a fail-closed 403: the
      // URL stops naming the conversation.
      const url = urlRef.current;
      if (url.conversationParam !== undefined) {
        navigate({ pathname: '/ask-genie', search: url.search }, { replace: true });
      }
      setConversationId(null);
      setQuestion('');
      setActiveAssetPath(null);
      setActionStatus(null);
      clearGenieTurns();
    };
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    return () => {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    };
  }, [navigate]);

  /**
   * Start a turn. `startedAt` is read (`Date.now()`) at the event site: the
   * React Compiler cannot prove a component-scope function runs only from
   * event handlers. The store refuses a second turn while one runs (either
   * surface's); the composer keeps the asked question until it lands.
   */
  function ask(q: string, followUpConversationId: string | null | undefined, startedAt: number) {
    const trimmed = q.trim();
    if (!trimmed) return;
    const activeConversationId = followUpConversationId ?? conversationId;
    if (!startGenieTurn({ question: trimmed, conversationId: activeConversationId, surface: 'route', startedAt })) {
      return;
    }
    setQuestion(q);
    setConversationId(activeConversationId);
    setActiveAssetPath(null);
    setActionStatus(null);
    if (!activeConversationId) {
      clearGenieConversationState();
    }
  }

  function newConversation() {
    if (getGenieTurnSnapshot().inFlight) return;
    suppressBootstrapConversationRef.current = true;
    setConversationId(null);
    setActiveAssetPath(null);
    setQuestion('');
    clearGenieTurns();
    clearGenieTurnNotes();
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
   *
   * A shape-valid id goes into the URL (audit shell-03): the read primes the
   * link's cache FIRST, then the URL is replaced, and the link adopts it from
   * the cache after the URL commit, with no second request. Any other id
   * (legacy fixture ids) is adopted directly and never enters the URL.
   */
  function loadSession(conversationIdToLoad: string, turns: GenieTurn[]) {
    if (getGenieTurnSnapshot().inFlight) return;
    const conversation = { conversationId: conversationIdToLoad, turns };
    setQuestion('');
    setActiveAssetPath(null);
    setActionStatus(null);
    const linkable = isGenieConversationId(conversationIdToLoad);
    if (linkable) queryClient.setQueryData(genieConversationQueryKey(conversationIdToLoad), conversation);
    if (linkable && conversationParam !== conversationIdToLoad) {
      navigate({ pathname: `/ask-genie/${conversationIdToLoad}`, search, hash }, { replace: true });
      return;
    }
    suppressBootstrapConversationRef.current = true;
    hydratedIdRef.current = linkable ? conversationIdToLoad : null;
    adoptGenieConversation(conversation);
    setConversationId(conversationIdToLoad);
    if (!linkable && conversationParam !== undefined) {
      navigate({ pathname: '/ask-genie', search, hash }, { replace: true });
    }
  }

  /** Leave a link that shows no conversation (not-found / unavailable). */
  function openAskGenie() {
    focusComposerRef.current = true;
    navigate({ pathname: '/ask-genie', search }, { replace: true });
  }

  function scopeToTrustedAsset(asset: { label: string; path: string }) {
    const scopedQuestion = buildTrustedAssetQuestion(asset);
    setDrawer(descriptorFor(asset.path));
    setQuestion(scopedQuestion);
    setActiveAssetPath(asset.path);
    questionRef.current?.focus();
  }

  /** A governed action, bound to the turn it was offered on (never the
   *  latest answer). Pessimistic: the status says what the server recorded. */
  function runAction(action: GenieActionSuggestion, payload: GenieAnswerShape) {
    setActionStatus(`Running ${action.label.toLowerCase()}...`);
    return runGenieActionRequest(action, payload, conversationId).then((outcome) => {
      const message = outcome.kind === 'ok' ? genieActionConfirmation(outcome.result) : outcome.message;
      setActionStatus(message);
      announceGenie(message);
      if (outcome.kind !== 'ok') return;
      if (action.action_type === 'save_borrowers') refreshWorkspace();
      if (outcome.result.route) navigate(outcome.result.route);
    });
  }

  const openRoute = (route: string) => navigate(route);

  return (
    <PageShell
      eyebrow="Genie"
      title="Ask Genie"
      lede="Ask about your book in plain language: coverage, segments, borrowers and market shifts. Answers drawn from your data show the figures behind them and where they came from, and any follow-up action still needs your approval."
      heroRight={<AskGenieTabs tab={tab} onSelect={selectTab} />}
    >
      <GenieAnnouncerRegion surface="route" visible={tab === 'ask'} />
      <section
        role="tabpanel"
        id={askGeniePanelId('ask')}
        aria-labelledby={askGenieTabId('ask')}
        hidden={tab !== 'ask'}
      >
        {held && (
          <p className="muted fs-12" data-genie-link="held">
            Opens after the current answer lands.
          </p>
        )}
        <div className="layoutA-grid">
          {link.kind === 'loading' || link.kind === 'not-found' || link.kind === 'unavailable' ? (
            <GenieConversationLinkPlaceholder
              state={link.kind}
              onOpenAskGenie={openAskGenie}
              onRetry={link.kind === 'unavailable' ? link.retry : () => undefined}
              retrying={link.kind === 'unavailable' && link.retrying}
            />
          ) : (
            <AskGenieAnswerPanel
              questionRef={questionRef}
              question={question}
              onQuestionChange={(value) => {
                setQuestion(value);
                setActiveAssetPath(null);
              }}
              onAsk={(q) => ask(q, undefined, Date.now())}
              onNewThread={newConversation}
              onLoadSession={loadSession}
              sampleQuestions={sampleQuestions}
              onFollowUp={(q, followUpConversationId) => ask(q, followUpConversationId, Date.now())}
              onAction={runAction}
              onEditQuestion={(q) => {
                setQuestion(q);
                setActiveAssetPath(null);
                questionRef.current?.focus();
              }}
              actionStatus={actionStatus}
              sourceAssets={genieStartQuery.data?.trusted_assets ?? []}
            />
          )}

          <div className="stack-grid">
            <div className="surface">
              <div className="surface__hdr">
                <Icon name="layers" size={14} className="icon-accent" />
                <h2 className="h-4">Trusted sources</h2>
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
      </section>
      <section
        role="tabpanel"
        id={askGeniePanelId('workflows')}
        aria-labelledby={askGenieTabId('workflows')}
        hidden={tab !== 'workflows'}
      >
        <GrowthAgentPanel agent={growthAgent} onOpenRoute={openRoute} />
      </section>
      <section
        role="tabpanel"
        id={askGeniePanelId('monitors')}
        aria-labelledby={askGenieTabId('monitors')}
        hidden={tab !== 'monitors'}
      >
        <GrowthAgentMonitorsPanel
          agent={growthAgent}
          onOpenRoute={openRoute}
          onOpenWorkflows={() => {
            selectTab('workflows');
            // The button hides its own panel; focus the Workflows tab (always
            // in the tablist) so keyboard focus does not fall to <body>.
            document.getElementById(askGenieTabId('workflows'))?.focus();
          }}
        />
      </section>
    </PageShell>
  );
}
