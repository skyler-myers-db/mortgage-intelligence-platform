import { useState } from 'react';
import { api } from '../lib/api';
import { PageShell } from '../components/layout/PageShell';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { DRAWER_SOURCES } from '../mocks/demoData';

/**
 * Ask Genie — deep-dive view with trusted-asset list and sample questions.
 * The floating GenieChat in the AppShell is the "ask anywhere" entry point;
 * this route is the curated walkthrough for stakeholders who want to see
 * which UC metric views Genie is grounded on.
 */

const SAMPLE_QUESTIONS = [
  'Which zips have the most in-the-money refi candidates?',
  'Show HELOC candidates with recent permits and strong equity.',
  'How many current customers show retention risk this week?',
  'Which segment converts best among owner-occupied under 50% LTV?',
];

const TRUSTED_ASSETS = [
  'mip_demo.gold.lead_population',
  'mip_demo.gold.lead_segment_membership',
  'mip_demo.gold.lead_scores',
  'mip_demo.gold.evidence_events',
  'mip_demo.semantics.lead_generation_metric_view',
];

export default function AskGenie() {
  const [question, setQuestion] = useState(SAMPLE_QUESTIONS[0]);
  const [answer, setAnswer] = useState('');
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(false);

  async function ask(q: string) {
    setQuestion(q);
    setLoading(true);
    try {
      const res = await api.genie(q);
      setAnswer((res as { answer?: string }).answer ?? '');
      setSource((res as { source?: string }).source ?? '');
    } finally {
      setLoading(false);
    }
  }

  return (
    <PageShell
      eyebrow="Ask Genie"
      title="Conversational analytics over curated Module 0 gold tables"
      lede="Genie is grounded on Unity Catalog metric views. Every answer cites the table and signal it used; tap any evidence chip to see lineage."
      heroRight={<Chip variant="neutral" icon="sparkle">Production: Databricks Genie API</Chip>}
    >
      <div className="layoutA-grid">
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="sparkle" size={14} style={{ color: 'var(--accent)' }} />
            <div className="h-4">Ask a question</div>
          </div>
          <div className="surface__body">
            <textarea
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
              <Button variant="primary" icon="send" onClick={() => ask(question)} disabled={loading}>
                {loading ? 'Asking…' : 'Ask Genie'}
              </Button>
            </div>
            {answer && (
              <div
                className="surface"
                style={{ marginTop: 16, background: 'var(--bg-1)' }}
              >
                <div className="surface__body">
                  <p style={{ margin: 0, fontSize: 14, color: 'var(--text-1)' }}>{answer}</p>
                  {source && (
                    <div style={{ marginTop: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span className="muted" style={{ fontSize: 11 }}>Source:</span>
                      <EvidenceChip source={DRAWER_SOURCES.nbo}>{source}</EvidenceChip>
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
              {TRUSTED_ASSETS.map((x) => (
                <div
                  key={x}
                  style={{
                    padding: '8px 10px',
                    background: 'var(--bg-1)',
                    border: '1px solid var(--line-1)',
                    borderRadius: 6,
                  }}
                >
                  <span className="mono" style={{ fontSize: 12, color: 'var(--text-1)' }}>{x}</span>
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
