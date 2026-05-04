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
  // The backend now emits three distinct source values:
  //   "genie"             — answer came from the live Genie space.
  //   "computed_fallback" — Genie was unreachable; answer was computed
  //                         deterministically from gold tables at request
  //                         time. The number is REAL (live SQL), not a
  //                         catalog literal — but the user should be able
  //                         to tell at a glance that this is a local
  //                         fallback, not a live Genie answer. We render
  //                         these chips with the warning variant +
  //                         explicit "Local SQL fallback" copy.
  //   "degraded"          — Genie + computed-fallback both unavailable.
  //                         Honest "warming up" message with no numbers.
  // 2026-05-04 follow-up to user feedback Q1 ("How is Genie figuring this
  // out?"). The user must always know whether they're reading a Genie
  // answer or a local SQL aggregation.
  const isComputedFallback = sourceLabel === 'computed_fallback';
  const isDegraded = sourceLabel === 'degraded';
  const sourceChip = (() => {
    if (isComputedFallback) {
      const asset = payload?.trusted_assets?.[0] ?? 'gold tables';
      return `Local SQL · ${asset}`;
    }
    if (isDegraded) {
      return 'Genie reconnecting';
    }
    return sourceLabel || (payload?.trusted_assets?.[0] ?? '');
  })();
  const sourceChipTitle = isComputedFallback
    ? 'Genie is reconnecting; this answer was computed from a deterministic local SQL aggregation against the gold table cited. The number is live, not a catalog literal.'
    : isDegraded
      ? 'The Genie space is warming up. Live answers will resume shortly. No curated answers are served while it reconnects.'
      : undefined;
  const sourceChipVariant: 'warning' | 'neutral' | undefined =
    isComputedFallback || isDegraded ? 'warning' : undefined;
  // Map the Genie-provided source label to the best matching drawer entry.
  // Previously the chip always opened DRAWER_SOURCES.nbo regardless of the
  // actual source -- a parity bug on the evidence path. Fallback is `nbo`
  // so unrecognized sources still open a meaningful drawer rather than
  // silently dropping the click. Computed-fallback / degraded chips don't
  // route into the drawer (the chip itself is the explanation).
  const drawerForSource =
    /itm|rules/i.test(sourceChip) ? DRAWER_SOURCES.itm
      : /permit/i.test(sourceChip) ? DRAWER_SOURCES.permit
      : /lead_population|population|borrower_360/i.test(sourceChip) ? DRAWER_SOURCES.population
      : /config/i.test(sourceChip) ? DRAWER_SOURCES.config
      : DRAWER_SOURCES.nbo;

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
                  <GenieAnswer payload={payload} onFollowUp={ask} />
                  {sourceChip && (
                    <div style={{ marginTop: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span className="muted" style={{ fontSize: 11 }}>Source:</span>
                      {sourceChipVariant === 'warning' ? (
                        // Computed-fallback / degraded: render a non-clickable
                        // warning chip with an explanatory tooltip so the user
                        // can tell the source from a live Genie answer at a
                        // glance. We don't open the drawer because the chip
                        // text already names the table; clicking would just
                        // open the population drawer which isn't the right
                        // story here.
                        <Chip
                          variant="warning"
                          icon={isComputedFallback ? 'bolt' : 'info'}
                          title={sourceChipTitle}
                        >
                          {sourceChip}
                        </Chip>
                      ) : (
                        <EvidenceChip source={drawerForSource}>{sourceChip}</EvidenceChip>
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
