import { useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import type { DrawerSource } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { Reveal } from '../components/fx/Reveal';
import { DRAWER_SOURCES } from '../lib/drawerSources';
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
  const { id } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);
  const [rec, setRec] = useState<OfferRecommendation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [auditId, setAuditId] = useState<string | null>(null);
  const { setApproval, approvals, lender } = useApp();
  const approval = id ? approvals[id] : undefined;

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setB(null);
    setRec(null);
    setLoadError(null);
    Promise.all([api.borrower(id), api.recommendOffer(id)])
      .then(([borrower, recommendation]) => {
        if (cancelled) return;
        setB(borrower);
        setRec(recommendation);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(
          err instanceof Error
            ? `Couldn't load borrower or offer: ${err.message}`
            : "Couldn't load borrower or offer.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Offer Orchestrator is a per-borrower action page; without an id
  // there's nothing to draft an outreach for. Redirect to lead queue.
  if (!id) {
    return <Navigate to="/lead-queue" replace />;
  }

  // Marketing-approved outreach copy. The "[first name]" placeholder is
  // intentionally left for the CRM to fill at send-time — we never store
  // or expose borrower PII in the UI. The body references the borrower's
  // city/state for local relevance but avoids internal thresholds or
  // engineering jargon ("bps", "cross-sell", etc.).
  const productLabel = rec?.product_label ?? b?.recommended_offer ?? '…';
  const defaultDraft = b
    ? `Hi [first name],

Our records show you may have a strong opportunity to reduce your monthly payment or access equity through a refinance or home equity line of credit. Based on current rates and your home's estimated value in ${b.city}, ${b.state}, your loan is a strong candidate for a ${productLabel.toLowerCase()}.

If you'd like to explore, a licensed ${lender} loan officer can walk you through the numbers — no obligation.

Reply or call 1-800-XXX-XXXX.`
    : '';

  const onApprove = async () => {
    setApproveError(null);
    try {
      const res = await api.approve(id);
      if (res.approved) {
        setApproval(id, 'approved');
        setAuditId(res.audit_event_id ?? null);
      } else {
        setApproveError('Approval endpoint returned approved=false.');
      }
    } catch (err: unknown) {
      setApproveError(
        err instanceof Error
          ? `Couldn't write approval: ${err.message}`
          : "Couldn't write approval.",
      );
    }
  };

  const onReject = () => {
    setApproval(id, 'rejected');
  };

  if (loadError) {
    return (
      <PageShell
        eyebrow="Next-Best-Offer + Outreach"
        title={`Couldn't load ${id}`}
        lede={loadError}
      >
        <div className="surface">
          <div className="surface__body" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <Chip variant="danger" icon="cross">Backend unavailable</Chip>
            <Link className="btn" to="/lead-queue">
              Back to lead queue
            </Link>
          </div>
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell
      eyebrow="Next-Best-Offer + Outreach"
      title="Convert intelligence into a human-approved action"
      lede="The draft below is never auto-sent. Loan officers approve or reject each message; approvals land in the immutable audit trail and release into the outreach channel for the next scheduled send."
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
            {rec ? (
              <p className="body" style={{ marginTop: 'var(--sp-2)' }}>
                {rec.rationale ?? b?.why_now}
              </p>
            ) : (
              <div style={{ marginTop: 'var(--sp-2)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <Skeleton width="100%" height={14} rounded="sm" />
                <Skeleton width="92%" height={14} rounded="sm" />
                <Skeleton width="78%" height={14} rounded="sm" />
              </div>
            )}
            <div style={{ marginTop: 'var(--sp-3)', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="muted" style={{ fontSize: 11 }}>Sources:</span>
              {rec
                ? rec.sources.map((s, idx) => {
                    // Prefer the backend-supplied human-readable label
                    // (added 2026-04-22). Fall back to the trailing UC
                    // segment so anything that hasn't been mapped still
                    // reads sensibly.
                    const label = rec.source_labels?.[idx]?.display_label
                      ?? shortSourceLabel(s);
                    return (
                      <EvidenceChip key={s} source={sourceDescriptor(s)}>
                        {label}
                      </EvidenceChip>
                    );
                  })
                : Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} width={96} height={18} rounded="sm" />
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
              aria-label="Outreach draft — review only"
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
            <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
              First-name placeholder fills from CRM at send time. Phone number is a lender default — replace with the campaign&apos;s tracking number.
            </div>
            <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Chip variant="neutral" icon="shield">Email channel</Chip>
              <Chip variant="neutral">LO call follow-up within 5 days</Chip>
            </div>
          </div>
        </div>
      </div>

      <Reveal className="layoutA-grid" style={{ marginTop: 'var(--gap-grid)' }}>
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
      </Reveal>

      <div style={{ marginTop: 'var(--gap-grid)' }}>
        <ApprovalBanner
          text={`${b ? `Borrower ${b.borrower_id}` : 'Borrower'} queued — approve to write an audit event and queue for outreach. Nothing is sent until you approve.`}
          onApprove={onApprove}
          onReject={onReject}
          disabled={approval === 'approved'}
        />
      </div>

      {approval === 'approved' && (
        <div className="surface" style={{ marginTop: 'var(--gap-grid)' }}>
          <div className="surface__body" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="burst" style={{ display: 'inline-flex' }}>
              <Chip variant="success" icon="check">Approved and logged to audit</Chip>
            </span>
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
      {approveError && (
        <div
          className="surface"
          role="alert"
          style={{ marginTop: 'var(--gap-grid)', borderColor: 'var(--signal-danger)' }}
        >
          <div className="surface__body" style={{ color: 'var(--signal-danger)' }}>
            {approveError}
          </div>
        </div>
      )}
    </PageShell>
  );
}
