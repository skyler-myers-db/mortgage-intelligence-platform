import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import type { PortfolioPreview } from '../types';
import { KpiCard } from '../components/mortgage/KpiCard';

const FILTERS = [
  'Georgia / Atlanta MSA / 30309',
  'Owner-occupied',
  'Open first lien',
  'All lender relationships',
  'Refi + HELOC',
  'Equity ≥ 15%',
];

export default function PortfolioBuilder() {
  const [preview, setPreview] = useState<PortfolioPreview | null>(null);

  useEffect(() => {
    api.portfolioPreview().then(setPreview);
  }, []);

  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Module 0 / Lead Portfolio Builder</div>
        <h1 className="h1">Build a high-intent borrower population</h1>
        <p className="muted">
          Start with public-record property and lien data. Add ownership, market, listing, permit, and lender
          relationship filters.
        </p>
      </div>
      <div className="card card-body grid grid-3">
        {FILTERS.map((x) => (
          <button className="button" key={x}>{x}</button>
        ))}
      </div>
      <div className="grid grid-4">
        <KpiCard
          label="Marketable population"
          value={(preview?.marketable_population ?? 0).toLocaleString()}
          delta="4.8% vs. last run"
        />
        <KpiCard
          label="High-intent leads"
          value={(preview?.high_intent_leads ?? 0).toLocaleString()}
          delta="18%"
        />
        <KpiCard
          label="Cost/contact"
          value={`$${preview?.cost_per_contact ?? 0}`}
          delta="0.11 lower"
        />
        <KpiCard
          label="Contact → app"
          value={`${preview?.projected_contact_to_app ?? 0}%`}
          delta="1.2 pp"
        />
      </div>
      <Link className="button primary" to="/segment-intelligence">Generate Portfolio</Link>
    </section>
  );
}
