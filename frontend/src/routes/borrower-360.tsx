import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type } from '../types';
import { currency } from '../lib/formatters';
import { TriggerTimeline } from '../components/mortgage/TriggerTimeline';

export default function Borrower360() {
  const { id = 'B-48291' } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);

  useEffect(() => {
    api.borrower(id).then(setB);
  }, [id]);

  if (!b) return <section className="page">Loading…</section>;

  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Borrower 360 / Property Dossier</div>
        <h1 className="h1">{b.display_name}</h1>
        <span className="score">{b.opportunity_score}</span>{' '}
        <span className="chip warning">Approval pending</span>
      </div>
      <div className="grid grid-3">
        <div className="card card-body">
          <div className="eyebrow">Borrower profile</div>
          <p><strong>{b.city}, {b.state} {b.zip}</strong></p>
          <p className="mono muted">{b.borrower_id}</p>
          <p>
            {b.segment_codes.map((s) => (
              <span className="chip" key={s} style={{ marginRight: 4 }}>{s}</span>
            ))}
          </p>
        </div>
        <div className="card card-body">
          <div className="eyebrow">Property dossier</div>
          <p className="mono">{b.clip_id}</p>
          <p>{b.subject_property}</p>
          <p>AVM {currency(b.avm_value)} · LTV {b.ltv}% · Equity {currency(b.equity_estimate)}</p>
          <button className="evidence-chip">AVM / lien evidence</button>
        </div>
        <div className="card card-body">
          <div className="eyebrow">Owner Link / Customer 360</div>
          <p className="mono">{b.owner_link_id}</p>
          <p>Related properties: {b.related_property_count}</p>
          <p>Recommended offer: <strong>{b.recommended_offer}</strong></p>
        </div>
      </div>
      <div className="grid grid-2">
        <div className="card card-body">
          <h2 className="h2">Trigger timeline</h2>
          <TriggerTimeline events={b.trigger_timeline} />
        </div>
        <div className="card card-body">
          <h2 className="h2">Why we trust this</h2>
          {b.evidence_events.map((e) => (
            <p key={e.evidence_id}>
              <button className="evidence-chip">{e.source_product}</button> {e.display_text}
            </p>
          ))}
          <Link className="button primary" to={`/offer-orchestrator/${b.borrower_id}`}>
            Build next-best-offer
          </Link>
        </div>
      </div>
    </section>
  );
}
