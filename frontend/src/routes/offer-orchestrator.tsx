import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { DRAWER_SOURCES } from '../mocks/demoData';
import { useApp } from '../components/AppContext';

/**
 * Offer Orchestrator — convert the borrower intelligence into a drafted
 * message (never auto-sent) that a human approves before Lakeflow posts to
 * marketing. Approval state flows into AppContext so the Lead Queue chip and
 * audit log stay in sync.
 */

export default function OfferOrchestrator() {
  const { id = 'B-48291' } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);
  const [auditId, setAuditId] = useState<string | null>(null);
  const { setApproval, approvals, lender } = useApp();
  const approval = approvals[id];

  useEffect(() => {
    api.borrower(id).then(setB);
  }, [id]);

  const primaryName = b?.display_name.split(' & ')[0] ?? 'there';
  const defaultDraft = b
    ? `Hi ${primaryName} — based on recent public-record signals in ${b.city}, ${b.state}, ${lender} may be able to help you evaluate ${b.recommended_offer.toLowerCase()} options. ${b.why_now} Reply if you'd like a licensed officer to follow up.`
    : '';

  const onApprove = async () => {
    const res = await api.approve(id);
    if (res.approved) {
      setApproval(id, 'approved');
      setAuditId(res.audit_event_id ?? null);
    }
  };

  const onReject = () => {
    setApproval(id, 'rejected');
  };

  return (
    <PageShell
      eyebrow="Next-Best-Offer + Outreach"
      title="Convert intelligence into a human-approved action"
      lede="The draft below is never auto-sent. Operators approve or reject each message; approvals are logged to the immutable audit trail and flow through Lakeflow into the marketing channel."
      heroRight={
        b && (
          <>
            <ScoreBadge value={b.opportunity_score} />
            <ConfidenceMeter value={b.confidence} />
          </>
        )
      }
    >
      <div className="layoutA-grid">
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="bolt" size={14} style={{ color: 'var(--accent)' }} />
            <div className="h-4">Primary offer</div>
          </div>
          <div className="surface__body">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>
                {b?.recommended_offer ?? '…'}
              </div>
              {b && <ScoreBadge value={b.opportunity_score} />}
            </div>
            <p className="body" style={{ marginTop: 8 }}>{b?.why_now ?? 'Loading rationale…'}</p>
            <div style={{ marginTop: 12, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="muted" style={{ fontSize: 11 }}>Evidence:</span>
              <EvidenceChip source={DRAWER_SOURCES.itm}>rules.itm_v3</EvidenceChip>
              <EvidenceChip source={DRAWER_SOURCES.nbo}>mlflow.mtg_nbo_v3</EvidenceChip>
              <EvidenceChip source={DRAWER_SOURCES.permit}>permits.building</EvidenceChip>
            </div>
          </div>
        </div>
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="doc" size={14} style={{ color: 'var(--accent)' }} />
            <div className="h-4">Draft outreach · review only, never auto-sent</div>
          </div>
          <div className="surface__body">
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
                fontSize: 13,
                lineHeight: 1.6,
                resize: 'vertical',
              }}
            />
            <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Chip variant="neutral" icon="shield">Email channel</Chip>
              <Chip variant="neutral">LO call follow-up within 5 days</Chip>
            </div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 'var(--gap-grid)' }}>
        <ApprovalBanner
          text={`${b?.display_name ?? 'Borrower'} queued — approve to write an audit event and queue for outreach. Nothing is sent until you approve.`}
          onApprove={onApprove}
          onReject={onReject}
          disabled={approval === 'approved'}
        />
      </div>

      {approval === 'approved' && (
        <div className="surface" style={{ marginTop: 'var(--gap-grid)' }}>
          <div className="surface__body" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Chip variant="success" icon="check">Approved and logged to audit</Chip>
            {auditId && <span className="mono muted" style={{ fontSize: 11 }}>audit: {auditId}</span>}
          </div>
        </div>
      )}
      {approval === 'rejected' && (
        <div className="surface" style={{ marginTop: 'var(--gap-grid)' }}>
          <div className="surface__body">
            <Chip variant="danger" icon="cross">Rejected — no outreach queued</Chip>
          </div>
        </div>
      )}
    </PageShell>
  );
}
