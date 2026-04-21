import { Link } from 'react-router-dom';

const modules = [
  { code: 'M0', title: 'Top-of-Funnel Lead Generation', status: 'Active', desc: 'Who should we contact, why now, and with what offer.' },
  { code: 'M1', title: 'Pipeline Optimization', status: 'Next', desc: 'Lead → app → approval throughput and stalls.' },
  { code: 'M2', title: 'LO Workbench', status: 'Later', desc: 'Officer assist with explainable next-best-action.' },
  { code: 'M3', title: 'Underwriting Copilot', status: 'Later', desc: 'Condition handling and exception triage.' },
  { code: 'M4', title: 'Risk & Retention', status: 'Later', desc: 'Portfolio-level retention and recapture.' },
];

export default function Home() {
  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Mortgage Intelligence Platform</div>
        <h1 className="h1">Build portfolio. Segment. Rank. Explain. Recommend. Approve. Audit.</h1>
        <p className="muted">
          Cotality public-record, lien, ownership, listing, permit, AVM, and market-analytics data, surfaced through
          Databricks Unity Catalog and governed by human approval. Module 0 ships today; Modules 1–4 extend the same
          spine.
        </p>
      </div>
      <div className="grid grid-4">
        <div className="card kpi">
          <div className="kpi-label">Marketable population</div>
          <div className="kpi-value num">89,553</div>
          <div className="delta">▲ 4.8% vs. last run</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">High-intent leads</div>
          <div className="kpi-value num">12,840</div>
          <div className="delta">▲ 18%</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">Cost / contact</div>
          <div className="kpi-value num">$2.18</div>
          <div className="delta">▲ 0.11 lower</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">Contact → app</div>
          <div className="kpi-value num">9.7%</div>
          <div className="delta">▲ 1.2 pp</div>
        </div>
      </div>
      <div className="grid grid-3">
        {modules.map((m) => (
          <div className="card card-body" key={m.code}>
            <div className="eyebrow">{m.code} · {m.status}</div>
            <h2 className="h2">{m.title}</h2>
            <p className="muted">{m.desc}</p>
          </div>
        ))}
      </div>
      <Link className="button primary" to="/portfolio-builder">Start: build a lead portfolio</Link>
    </section>
  );
}
