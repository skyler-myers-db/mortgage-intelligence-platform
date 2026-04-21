import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type } from '../types';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';

export default function OfferOrchestrator() {
  const { id = 'B-48291' } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);
  const [approved, setApproved] = useState(false);
  const [auditId, setAuditId] = useState<string | null>(null);

  useEffect(() => {
    api.borrower(id).then(setB);
  }, [id]);

  const primaryName = b?.display_name.split(' & ')[0] ?? 'there';
  const defaultDraft = b
    ? `Hi ${primaryName} — based on recent public-record signals in ${b.city}, ${b.state}, Summit Mortgage may be able to help you evaluate ${b.recommended_offer.toLowerCase()} options. ${b.why_now} Reply if you'd like a licensed officer to follow up.`
    : '';

  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Next-Best-Offer + Outreach</div>
        <h1 className="h1">Convert intelligence into a human-approved action</h1>
      </div>
      <div className="grid grid-2">
        <div className="card card-body">
          <h2 className="h2">Primary offer: {b?.recommended_offer ?? '…'}</h2>
          <p className="muted">{b?.why_now ?? 'Loading rationale…'}</p>
          <span className="score">{b?.opportunity_score ?? 0}</span>
          <p style={{ marginTop: 12 }}>
            <button className="evidence-chip">rules.itm_v3</button>{' '}
            <button className="evidence-chip">offer_rules_v1</button>
          </p>
        </div>
        <div className="card card-body">
          <h2 className="h2">Draft outreach (review only — never auto-sent)</h2>
          <textarea
            key={b?.borrower_id ?? 'empty'}
            defaultValue={defaultDraft}
            style={{
              width: '100%',
              minHeight: 180,
              background: 'var(--bg-1)',
              color: 'var(--text-1)',
              border: '1px solid var(--line-1)',
              borderRadius: 8,
              padding: 12,
              fontFamily: 'var(--font-sans)',
            }}
          />
        </div>
      </div>
      <ApprovalBanner
        onApprove={async () => {
          const res = await api.approve(id);
          if (res.approved) {
            setApproved(true);
            setAuditId(res.audit_event_id ?? null);
          }
        }}
      />
      {approved && (
        <div className="card card-body">
          <span className="chip success">Approved and logged to audit</span>
          {auditId && <p className="mono muted" style={{ marginTop: 8 }}>audit: {auditId}</p>}
        </div>
      )}
    </section>
  );
}
