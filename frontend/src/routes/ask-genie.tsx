import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../types';
import { useApp } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GenieAnswer } from '../components/mortgage/GenieAnswer';
import { GenieProgress } from '../components/mortgage/GenieProgress';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { DRAWER_SOURCES } from '../lib/drawerSources';

/**
 * Ask Genie — deep-dive view with trusted-asset list and sample questions.
 * The floating GenieChat in the AppShell is the "ask anywhere" entry point;
 * this route is the curated walkthrough for stakeholders who want to see
 * which UC metric views Genie is grounded on. Answer rendering is delegated
 * to the shared <GenieAnswer> so metric_value / table_rows / follow_ups
 * surface identically here and in the floating chat.
 */

const SAMPLE_QUESTIONS = [
  'Which zips have the most in-the-money refi candidates?',
  'Show HELOC candidates with strong equity and explain the pending permit-feed gap.',
  'How many current customers show retention risk this week?',
  'Which segment converts best among owner-occupied under 50% LTV?',
];

// Friendly-name + technical-path tuple. Friendly is what a business user
// reads; the UC path sits in the title tooltip for governance/ops.
const TRUSTED_ASSETS: Array<{ label: string; path: string }> = [
  { label: 'Borrower population',          path: 'mip.gold.lead_population' },
  { label: 'Segment rollups',              path: 'mip.gold.segment_population' },
  { label: 'Opportunity scores',           path: 'mip.gold.lead_scores' },
  { label: 'Borrower 360 profile',         path: 'mip.gold.borrower_360' },
  { label: 'Borrower dossier',             path: 'mip.gold.borrower_dossier' },
  { label: 'Source evidence',              path: 'mip.gold.evidence_events' },
  { label: 'Lock-in cohort',               path: 'mip.gold.lockin_cohort' },
  { label: 'Lead-generation metric view',  path: 'mip.semantics.lead_generation_metric_view' },
  { label: 'Segment performance view',     path: 'mip.semantics.segment_performance_metric_view' },
  { label: 'Borrower opportunity view',    path: 'mip.semantics.borrower_opportunity_metric_view' },
];

export default function AskGenie() {
  const navigate = useNavigate();
  const { refreshWorkspace } = useApp();
  const [question, setQuestion] = useState(SAMPLE_QUESTIONS[0]);
  const [conversationId, setConversationId] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem('mip.genie.conversationId');
    } catch {
      return null;
    }
  });

  useEffect(() => {
    if (conversationId) return undefined;
    const controller = new AbortController();
    api.genieStart(controller.signal)
      .then((result) => {
        if (!result.conversation_id) return;
        setConversationId(result.conversation_id);
        try {
          window.localStorage.setItem('mip.genie.conversationId', result.conversation_id);
        } catch {
          // ignore
        }
      })
      .catch(() => {
        // The first question will start a new Databricks Genie conversation.
      });
    return () => controller.abort();
  }, [conversationId]);
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  // `submittedQuestion` drives the warming-up-wrapped fetch. Typing in
  // the textarea updates `question`; clicking Ask commits the current
  // value into `submittedQuestion`, which triggers the hook. Pairing
  // with `submitToken` lets the same question be re-asked without
  // the hook no-op'ing on unchanged deps.
  const [submittedQuestion, setSubmittedQuestion] = useState<string | null>(null);
  const [submitToken, setSubmitToken] = useState<number>(0);

  const {
    data: payload,
    warmingUp,
    error,
    manualRetry,
  } = useWarmingUpRetry<GenieAnswerShape>(
    (signal) => api.genie(submittedQuestion ?? '', conversationId, signal) as Promise<GenieAnswerShape>,
    [submittedQuestion, submitToken],
    { enabled: submittedQuestion !== null && submittedQuestion.length > 0 },
  );

  const loading = submittedQuestion !== null && payload === null && warmingUp === null && error === null;
  const errorMsg = error
    ? error instanceof Error
      ? `Couldn't reach Genie: ${error.message}`
      : "Couldn't reach Genie."
    : null;

  function ask(q: string) {
    setQuestion(q);
    setSubmittedQuestion(q);
    setSubmitToken((n) => n + 1);
    setActionStatus(null);
  }

  function newConversation() {
    setConversationId(null);
    setSubmittedQuestion(null);
    setActionStatus('Started a new Genie thread.');
    try {
      window.localStorage.removeItem('mip.genie.conversationId');
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    if (!payload?.conversation_id) return;
    setConversationId(payload.conversation_id);
    try {
      window.localStorage.setItem('mip.genie.conversationId', payload.conversation_id);
    } catch {
      // ignore
    }
  }, [payload?.conversation_id]);

  async function runAction(action: GenieActionSuggestion) {
    setActionStatus(`Running ${action.label.toLowerCase()}...`);
    try {
      const result = await api.genieAction({
        ...action,
        conversation_id: payload?.conversation_id ?? conversationId,
        message_id: payload?.message_id ?? null,
        question_hash: payload?.question_hash ?? null,
      });
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
  // The backend emits two source values:
  //   "genie"    — answer came from the live Genie space.
  //   "degraded" — Genie is unreachable. Honest "warming up" message
  //                with no numbers. (The "computed_fallback" path that
  //                used a Python SQL template was removed 2026-05-04
  //                per user feedback: "we don't want this app to be
  //                gimmicky at all.")
  const isDegraded = sourceLabel === 'degraded';
  const isBlocked = sourceLabel === 'policy_blocked' || sourceLabel === 'refused' || sourceLabel === 'data_gap';
  const sourceChip = isDegraded
    ? 'Genie reconnecting'
    : isBlocked
      ? sourceLabel === 'refused'
        ? 'Prompt refused'
        : sourceLabel === 'data_gap'
          ? 'Source pending'
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
  // was confusing per 2026-05-04 user feedback). Computed-fallback /
  // degraded chips also render inert (the chip text is the explanation).
  const drawerForSource =
    /itm|rules/i.test(sourceChip) ? DRAWER_SOURCES.itm
      : /lead.?score|lead_scores|fn_lead_score/i.test(sourceChip) ? DRAWER_SOURCES.leadScore
      : /permit/i.test(sourceChip) ? DRAWER_SOURCES.permit
      : /lead_population|population|borrower_360/i.test(sourceChip) ? DRAWER_SOURCES.population
      : /next.?best|nbo/i.test(sourceChip) ? DRAWER_SOURCES.nbo
      : /config/i.test(sourceChip) ? DRAWER_SOURCES.config
      : null;

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
              aria-label="Ask Genie — question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
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
              className="route-textarea"
            />
            <div className="section-actions">
              <Button
                variant="primary"
                icon="send"
                onClick={() => ask(question)}
                disabled={loading || warmingUp !== null}
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
                  <GenieAnswer payload={payload} onFollowUp={ask} onAction={runAction} withChart />
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
              {TRUSTED_ASSETS.map((a) => (
                <div
                  key={a.path}
                  title={a.path}
                  className="trusted-asset"
                >
                  <div className="trusted-asset__label">{a.label}</div>
                  <div className="trusted-asset__path">{a.path}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="sparkle" size={14} className="icon-accent" />
              <div className="h-4">Suggested questions</div>
            </div>
            <div className="surface__body trusted-asset-list">
              {SAMPLE_QUESTIONS.map((q) => (
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
          </div>
        </div>
      </div>
    </PageShell>
  );
}
