import { useState } from 'react';
import { api } from '../lib/api';

const SAMPLE_QUESTIONS = [
  'Which zips have the most in-the-money refi candidates?',
  'Show HELOC candidates with recent permits and strong equity.',
  'How many current customers show retention risk this week?',
];

const TRUSTED_ASSETS = [
  'mip_demo.gold.lead_population',
  'mip_demo.gold.lead_segment_membership',
  'mip_demo.gold.lead_scores',
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
    const res = await api.genie(q);
    setAnswer(res.answer ?? '');
    setSource(res.source ?? '');
    setLoading(false);
  }

  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Ask Genie</div>
        <h1 className="h1">Conversational analytics over curated Module 0 gold tables</h1>
      </div>
      <div className="grid grid-2">
        <div className="card card-body">
          <h2 className="h2">Trusted assets</h2>
          {TRUSTED_ASSETS.map((x) => (
            <p className="mono" key={x}>{x}</p>
          ))}
          <h2 className="h2" style={{ marginTop: 20 }}>Suggested questions</h2>
          {SAMPLE_QUESTIONS.map((q) => (
            <p key={q}>
              <button className="evidence-chip" onClick={() => ask(q)}>{q}</button>
            </p>
          ))}
        </div>
        <div className="card card-body">
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
            }}
          />
          <br />
          <button className="button primary" onClick={() => ask(question)} disabled={loading}>
            {loading ? 'Asking…' : 'Ask Genie'}
          </button>
          {answer && (
            <div className="card card-body" style={{ marginTop: 12 }}>
              <p>{answer}</p>
              {source && <p className="mono muted">source: {source}</p>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
