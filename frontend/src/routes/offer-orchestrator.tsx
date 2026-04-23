import { useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { Reveal } from '../components/fx/Reveal';
import { descriptorFor } from '../lib/drawerSources';
import { useApp } from '../components/AppContext';

/** Short, presenter-friendly label that fits in a chip. */
function shortSourceLabel(source: string): string {
  return source.split('.').pop() ?? source;
}

/**
 * Module-scoped stale-while-revalidate cache for the three per-borrower
 * fetches (`api.borrower`, `api.recommendOffer`, `api.draftOutreach`).
 *
 * The backend's resilience layer already caches the portfolio preview,
 * but the per-borrower dossier path was unbuffered — navigating
 * back/forward between borrowers re-fired three API calls each trip.
 * This cache keeps a 5-minute TTL snapshot in memory so the user sees
 * instant hydration on revisit; the effect still re-fetches in the
 * background when the token increments so the data stays live.
 *
 * Hole-finder finding #23, 2026-04-23.
 */
interface BorrowerCacheEntry {
  borrower: Borrower360Type;
  recommendation: OfferRecommendation;
  draftBody: string | null;
  fetched: number;
}
const BORROWER_CACHE = new Map<string, BorrowerCacheEntry>();
const BORROWER_CACHE_TTL_MS = 5 * 60 * 1000;
function readBorrowerCache(id: string): BorrowerCacheEntry | null {
  const hit = BORROWER_CACHE.get(id);
  if (!hit) return null;
  if (Date.now() - hit.fetched > BORROWER_CACHE_TTL_MS) {
    BORROWER_CACHE.delete(id);
    return null;
  }
  return hit;
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
  // Reload token re-runs the borrower + recommend + draft fetches.
  // Hole-finder finding #1, 2026-04-23.
  const [reloadToken, setReloadToken] = useState<number>(0);
  // 2026-04-22: the draft textarea is now controlled and hydrated from
  // /api/outreach/draft so edits persist through approve (they were
  // being dropped on a JSX string literal before). `draftLoaded` tracks
  // whether the backend fetch succeeded so we can render the "default
  // template used" muted note when we fall back to the local string.
  const [draftBody, setDraftBody] = useState<string>('');
  const [draftLoaded, setDraftLoaded] = useState<boolean>(false);
  const { setApproval, approvals, lender } = useApp();
  const approval = id ? approvals[id] : undefined;

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    // SWR: if we have a cached snapshot that's still fresh, hydrate
    // from it immediately so navigating back to a borrower is instant.
    // We still refetch in the background so the data stays live.
    const cached = readBorrowerCache(id);
    if (cached && reloadToken === 0) {
      setB(cached.borrower);
      setRec(cached.recommendation);
      setLoadError(null);
      if (cached.draftBody && cached.draftBody.trim().length > 0) {
        setDraftBody(cached.draftBody);
        setDraftLoaded(true);
      } else {
        setDraftBody('');
        setDraftLoaded(false);
      }
    } else {
      setB(null);
      setRec(null);
      setLoadError(null);
      setDraftBody('');
      setDraftLoaded(false);
    }
    Promise.all([api.borrower(id), api.recommendOffer(id)])
      .then(([borrower, recommendation]) => {
        if (cancelled) return;
        setB(borrower);
        setRec(recommendation);
        // Merge into cache; draftBody gets updated by the parallel
        // fetch below.
        const prev = BORROWER_CACHE.get(id);
        BORROWER_CACHE.set(id, {
          borrower,
          recommendation,
          draftBody: prev?.draftBody ?? null,
          fetched: Date.now(),
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(
          err instanceof Error
            ? `Couldn't load borrower or offer: ${err.message}`
            : "Couldn't load borrower or offer.",
        );
      });
    // Fetch the backend-generated draft in parallel. On any failure we
    // silently keep `draftLoaded=false`; the render path falls back to
    // the hardcoded template and shows a muted note so the approver
    // knows the endpoint was unavailable. This matches the degraded-UI
    // posture in CLAUDE.md -- fail visible, never substitute silently.
    api
      .draftOutreach(id)
      .then((draft) => {
        if (cancelled) return;
        if (draft?.body && draft.body.trim().length > 0) {
          setDraftBody(draft.body);
          setDraftLoaded(true);
          const prev = BORROWER_CACHE.get(id);
          if (prev) {
            BORROWER_CACHE.set(id, {
              ...prev,
              draftBody: draft.body,
              fetched: Date.now(),
            });
          }
        }
      })
      .catch(() => {
        // swallow; fall back to defaultDraft below
      });
    return () => {
      cancelled = true;
    };
  }, [id, reloadToken]);

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
      // Forward the chosen offer_code + evidence_ids so the audit row
      // captures what the approver actually saw — not just the borrower
      // id. Falls back to the borrower's recommended_offer when the
      // recommendation hasn't hydrated yet.
      const offer_code = rec?.offer_code ?? b?.recommended_offer ?? null;
      const evidence_ids = rec?.evidence_ids ?? b?.evidence_ids ?? [];
      // Prefer whatever the approver actually has in front of them:
      // the controlled textarea's current value. Fall back to the
      // rendered default template so callers that approved without
      // touching the textarea still write durable copy into the audit
      // metadata.
      const draft_body =
        (draftLoaded ? draftBody : draftBody) || defaultDraft || null;
      const res = await api.approve(id, { offer_code, evidence_ids, draft_body });
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

  const onReject = async () => {
    setApproveError(null);
    try {
      // Audit finding 2026-04-22: reject used to be a local-state-only
      // mutation. Now it writes the same governed pair of rows the
      // approve path does (mip_app.approvals action='reject' +
      // mip_app.action_audit event_type='OUTREACH_REJECT') and fires
      // the same lifecycle-sync trigger so the funnel view reflects
      // the drop. Failures surface as a banner; state flips only on
      // confirmed success.
      const offer_code = rec?.offer_code ?? b?.recommended_offer ?? null;
      const evidence_ids = rec?.evidence_ids ?? b?.evidence_ids ?? [];
      const res = await api.reject(id, { offer_code, evidence_ids });
      if (res.rejected) {
        setApproval(id, 'rejected');
        setAuditId(res.audit_event_id ?? null);
      } else {
        setApproveError('Reject endpoint returned rejected=false.');
      }
    } catch (err: unknown) {
      setApproveError(
        err instanceof Error
          ? `Couldn't record rejection: ${err.message}`
          : "Couldn't record rejection.",
      );
    }
  };

  if (loadError) {
    return (
      <PageShell
        eyebrow="Offer & Outreach"
        title={`Couldn't load ${id}`}
        lede={loadError}
      >
        <div className="surface">
          <div className="surface__body" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <Chip variant="danger" icon="cross">Backend unavailable</Chip>
            <button
              type="button"
              className="btn"
              onClick={() => setReloadToken((n) => n + 1)}
              aria-label="Retry loading borrower and offer"
            >
              Retry
            </button>
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
      eyebrow="Offer & Outreach"
      title="Review and approve outreach"
      lede="Review the recommended offer, alternatives considered, thresholds applied, and the draft message. Approve to release the draft into the outreach queue; reject to drop the borrower."
      heroRight={
        b && (
          <>
            <ScoreBadge value={b.opportunity_score} />
            <ConfidenceMeter value={b.confidence} />
            {/* LO friction fix (2026-04-22): surface Approve in the hero so
                it is visible on 1366x768 laptops without scrolling. The
                row-detail Approve button further down is preserved. */}
            <Button
              variant="primary"
              size="sm"
              icon="check"
              onClick={() => void onApprove()}
              disabled={!rec || approval === 'approved'}
              aria-label={
                approval === 'approved'
                  ? `Borrower ${b.borrower_id} already approved`
                  : `Approve borrower ${b.borrower_id}`
              }
              data-testid="hero-approve"
            >
              {approval === 'approved' ? 'Approved' : 'Approve'}
            </Button>
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
                      <EvidenceChip key={s} source={descriptorFor(s)}>
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
            <div className="h-4">Draft outreach · review only</div>
          </div>
          <div className="surface__body">
            {!draftLoaded && b && (
              <div
                className="muted"
                data-testid="draft-fallback-note"
                style={{ marginBottom: 6, fontSize: 11 }}
              >
                Default template used — offer-draft endpoint unavailable.
              </div>
            )}
            <textarea
              key={b?.borrower_id ?? 'empty'}
              aria-label="Outreach draft — review only"
              value={draftLoaded ? draftBody : defaultDraft}
              onChange={(e) => {
                // Hydrate draftBody on first edit when the backend
                // draft never loaded, so subsequent edits accumulate
                // on top of the default template rather than reverting.
                if (!draftLoaded) setDraftLoaded(true);
                setDraftBody(e.target.value);
              }}
              data-testid="outreach-draft"
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
          text={`${b ? `Borrower ${b.borrower_id}` : 'Borrower'} pending review. Approve writes an audit event and releases the draft into the outreach queue.`}
          onApprove={() => void onApprove()}
          onReject={() => void onReject()}
          disabled={approval === 'approved' || approval === 'rejected'}
        />
      </div>

      {approval === 'approved' && (
        <div className="surface" style={{ marginTop: 'var(--gap-grid)' }}>
          <div className="surface__body" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="burst" style={{ display: 'inline-flex' }}>
              <Chip variant="success" icon="check">Approved · released to outreach queue</Chip>
            </span>
            {auditId && <span className="mono muted" style={{ fontSize: 11 }}>audit: {auditId}</span>}
          </div>
        </div>
      )}
      {approval === 'rejected' && (
        <div className="surface" style={{ marginTop: 'var(--gap-grid)' }}>
          <div className="surface__body">
            <Chip variant="danger" icon="cross">Rejected</Chip>
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
