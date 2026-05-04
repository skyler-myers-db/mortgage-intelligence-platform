import { useState } from 'react';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { GenieAnswer as GenieAnswerShape } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GenieAnswer } from '../components/mortgage/GenieAnswer';
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
  'Show HELOC candidates with recent permits and strong equity.',
  'How many current customers show retention risk this week?',
  'Which segment converts best among owner-occupied under 50% LTV?',
];

// Friendly-name + technical-path tuple. Friendly is what a business user
// reads; the UC path sits in the title tooltip for governance/ops.
const TRUSTED_ASSETS: Array<{ label: string; path: string }> = [
  { label: 'Borrower population',          path: 'mip.gold.lead_population' },
  { label: 'Segment membership',           path: 'mip.gold.lead_segment_membership' },
  { label: 'Opportunity scores',           path: 'mip.gold.lead_scores' },
  { label: 'Source evidence',              path: 'mip.gold.evidence_events' },
  { label: 'Lead-generation metric view',  path: 'mip.semantics.lead_generation_metric_view' },
];

export default function AskGenie() {
  const [question, setQuestion] = useState(SAMPLE_QUESTIONS[0]);
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
    (signal) => api.genie(submittedQuestion ?? '', signal) as Promise<GenieAnswerShape>,
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
  const sourceChip = isDegraded
    ? 'Genie reconnecting'
    : sourceLabel || (payload?.trusted_assets?.[0] ?? '');
  const sourceChipTitle = isDegraded
    ? 'The Genie space is warming up. Live answers will resume shortly.'
    : undefined;
  const sourceChipVariant: 'warning' | undefined = isDegraded ? 'warning' : undefined;
  // Map the Genie-provided source label to the best matching drawer entry.
  // Returns null when no specific match exists — the chip then renders as
  // an inert neutral chip rather than defaulting to NBO and misleading
  // the user into the wrong drawer (the prior "default to NBO" routing
  // was confusing per 2026-05-04 user feedback). Computed-fallback /
  // degraded chips also render inert (the chip text is the explanation).
  const drawerForSource =
    /itm|rules/i.test(sourceChip) ? DRAWER_SOURCES.itm
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
            <Icon name="sparkle" size={14} style={{ color: 'var(--accent)' }} />
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
              style={{
                width: '100%',
                minHeight: 90,
                background: 'var(--bg-1)',
                color: 'var(--text-1)',
                border: '1px solid var(--line-1)',
                borderRadius: 8,
                padding: 12,
                fontFamily: 'var(--font-sans)',
                fontSize: 14,
                resize: 'vertical',
              }}
            />
            <div style={{ marginTop: 10 }}>
              <Button
                variant="primary"
                icon="send"
                onClick={() => ask(question)}
                disabled={loading || warmingUp !== null}
              >
                {loading || warmingUp !== null ? 'Asking…' : 'Ask Genie'}
              </Button>
            </div>
            {warmingUp && (
              <div style={{ marginTop: 16 }}>
                <WarmingUpBlock state={warmingUp} title="Asking Genie" compact />
              </div>
            )}
            {errorMsg && !warmingUp && (
              <div
                className="surface"
                role="alert"
                style={{ marginTop: 16, background: 'var(--bg-1)', borderColor: 'var(--signal-danger)' }}
              >
                <div
                  className="surface__body"
                  style={{
                    color: 'var(--signal-danger)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 12,
                  }}
                >
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
            {payload && (
              <div
                className="surface"
                style={{ marginTop: 16, background: 'var(--bg-1)' }}
              >
                <div className="surface__body">
                  {/* withChart=true: opt this deep-dive view in to the
                      auto-detected bar chart for top-N / per-state-style
                      table_rows payloads. The floating bubble does NOT
                      pass this prop, so its compact form is unchanged. */}
                  <GenieAnswer payload={payload} onFollowUp={ask} withChart />
                  {sourceChip && (
                    <div style={{ marginTop: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span className="muted" style={{ fontSize: 11 }}>Source:</span>
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

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-grid)' }}>
          <div className="surface">
            <div className="surface__hdr">
              <Icon name="layers" size={14} style={{ color: 'var(--accent)' }} />
              <div className="h-4">Trusted assets</div>
            </div>
            <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {TRUSTED_ASSETS.map((a) => (
                <div
                  key={a.path}
                  title={a.path}
                  style={{
                    padding: '8px 10px',
                    background: 'var(--bg-1)',
                    border: '1px solid var(--line-1)',
                    borderRadius: 6,
                  }}
                >
                  <div style={{ fontSize: 13, color: 'var(--text-1)' }}>{a.label}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="sparkle" size={14} style={{ color: 'var(--accent)' }} />
              <div className="h-4">Suggested questions</div>
            </div>
            <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {SAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  type="button"
                  className="filter"
                  style={{ textAlign: 'left' }}
                  onClick={() => ask(q)}
                >
                  <Icon name="sparkle" size={11} />
                  <span style={{ color: 'var(--text-2)' }}>{q}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </PageShell>
  );
}
