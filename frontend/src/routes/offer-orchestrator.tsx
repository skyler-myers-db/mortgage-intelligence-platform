import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import type { DrawerSource } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { DRAWER_SOURCES } from '../mocks/demoData';
import { useApp } from '../components/AppContext';

/** Map a backend source table → DrawerSource. Falls back to a neutral descriptor
 *  so presenter can still click through and see the raw UC path. */
function sourceDescriptor(source: string): DrawerSource {
  const key = source.toLowerCase();
  if (key.includes('fn_in_the_money') || key.includes('itm')) return DRAWER_SOURCES.itm;
  if (key.includes('fn_next_best_offer') || key.includes('nbo')) return DRAWER_SOURCES.nbo;
  if (key.includes('permit')) return DRAWER_SOURCES.permit;
  if (key.includes('population') || key.includes('public_records')) return DRAWER_SOURCES.population;
  // Neutral fallback for fn_rate_spread, fn_lead_score, etc. — still clickable.
  return {
    title: source,
    short: source.split('.').pop() ?? source,
    description: `Unity Catalog object: ${source}. Click through for lineage once wired.`,
    lineage: [{ layer: 'UC', name: source }],
    signals: [],
  };
}

/** Short, presenter-friendly label that fits in a chip. */
function shortSourceLabel(source: string): string {
  return source.split('.').pop() ?? source;
}

/** Human-readable threshold labels. Keeps the "if you raised X here" story tangible. */
const THRESHOLD_LABELS: Record<string, string> = {
  min_spread_bps: 'Min spread (bps)',
  min_equity_pct: 'Min equity (%)',
  heloc_equity_min_pct: 'HELOC equity floor (%)',
  cashout_equity_min_pct: 'Cash-out equity floor (%)',
  retention_min_spread_bps: 'Retention min spread (bps)',
};

function humanizeThresholdKey(k: string): string {
  if (THRESHOLD_LABELS[k]) return THRESHOLD_LABELS[k];
  // Reasonable fallback: snake_case → Title Case
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Offer Orchestrator — convert the borrower intelligence into a drafted
 * message (never auto-sent) that a human approves before Lakeflow posts to
 * marketing. Approval state flows into AppContext so the Lead Queue chip and
 * audit log stay in sync.
 */

export default function OfferOrchestrator() {
  const { id = 'B-48291' } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);
  const [rec, setRec] = useState<OfferRecommendation | null>(null);
  const [auditId, setAuditId] = useState<string | null>(null);
  const { setApproval, approvals, lender } = useApp();
  const approval = approvals[id];

  useEffect(() => {
    setB(null);
    setRec(null);
    api.borrower(id).then(setB);
    api.recommendOffer(id).then(setRec);
  }, [id]);

  const primaryName = b?.display_name.split(' & ')[0] ?? 'there';
  const productLabel = rec?.product_label ?? b?.recommended_offer ?? '…';
  const defaultDraft = b
    ? `Hi ${primaryName} — based on recent public-record signals in ${b.city}, ${b.state}, ${lender} may be able to help you evaluate ${productLabel.toLowerCase()} options. ${rec?.rationale ?? b.why_now} Reply if you'd like a licensed officer to follow up.`
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
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-3)' }}>
              <div style={{ fontSize: 'var(--fs-22)', fontWeight: 600, letterSpacing: '-0.01em' }}>
                {productLabel}
              </div>
              {b && <ScoreBadge value={b.opportunity_score} />}
            </div>
            <p className="body" style={{ marginTop: 'var(--sp-2)' }}>
              {rec?.rationale ?? b?.why_now ?? 'Loading rationale…'}
            </p>
            <div style={{ marginTop: 'var(--sp-3)', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="muted" style={{ fontSize: 11 }}>Sources:</span>
              {(rec?.sources ?? []).map((s) => (
                <EvidenceChip key={s} source={sourceDescriptor(s)}>
                  {shortSourceLabel(s)}
                </EvidenceChip>
              ))}
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

      <div className="layoutA-grid" style={{ marginTop: 'var(--gap-grid)' }}>
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="doc" size={14} style={{ color: 'var(--accent)' }} />
            <div>
              <div className="eyebrow">Considered alternatives</div>
              <div className="h-4" style={{ marginTop: 2 }}>
                {rec ? `${rec.alternatives.length} other product${rec.alternatives.length === 1 ? '' : 's'} ruled out` : 'Loading…'}
              </div>
            </div>
          </div>
          <div className="surface__body">
            {rec && rec.alternatives.length === 0 && (
              <p className="muted body" style={{ margin: 0 }}>
                No alternatives considered — no trigger fires.
              </p>
            )}
            {rec && rec.alternatives.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
                {rec.alternatives.map((alt) => (
                  <div
                    key={alt.offer_code}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 'var(--sp-2)',
                      padding: 'var(--sp-3)',
                      background: 'var(--bg-1)',
                      border: '1px solid var(--line-1)',
                      borderRadius: 'var(--r-md)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-2)' }}>
                      <div style={{ fontWeight: 600, color: 'var(--text-1)' }}>{alt.product_label}</div>
                      <Chip variant="neutral" className="mono">{alt.offer_code}</Chip>
                    </div>
                    <p className="body muted" style={{ margin: 0 }}>{alt.reason_not_chosen}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="surface">
          <div className="surface__hdr">
            <Icon name="shield" size={14} style={{ color: 'var(--accent)' }} />
            <div>
              <div className="eyebrow">Thresholds applied</div>
              <div className="h-4" style={{ marginTop: 2 }}>Admin config at decision time</div>
            </div>
          </div>
          <div className="surface__body">
            {rec ? (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto',
                  rowGap: 'var(--sp-2)',
                  columnGap: 'var(--sp-3)',
                  alignItems: 'baseline',
                }}
              >
                {Object.entries(rec.thresholds_applied).map(([k, v]) => (
                  <div key={k} style={{ display: 'contents' }}>
                    <span className="muted body">{humanizeThresholdKey(k)}</span>
                    <span className="mono" style={{ fontSize: 'var(--fs-13)', color: 'var(--text-1)' }}>{v}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted body" style={{ margin: 0 }}>Loading thresholds…</p>
            )}
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
