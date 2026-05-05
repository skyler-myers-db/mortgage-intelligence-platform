import { useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api, isAbortError, isWarmingUpError, dependencyLabel } from '../lib/api';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
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
  // Cold-start warming-up state for the borrower + recommend fetch pair.
  // Shape matches WarmingUpBlock's contract. Non-null means we're in a
  // 503 retry loop and the UI should show "Warehouse warming up
  // (attempt N of 6)…" instead of the red error banner.
  const [warmingUp, setWarmingUp] = useState<WarmingUpState | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [auditId, setAuditId] = useState<string | null>(null);
  // R5-11 (2026-04-23): in-flight flag forwarded to ApprovalBanner so
  // the buttons disable while a POST is pending. Prevents a double
  // click from writing two audit rows.
  const [approving, setApproving] = useState<boolean>(false);
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
  // Cold-start warming-up state for the draftOutreach fetch. Non-null =
  // the draft endpoint is in a 503 retry loop (mirrors the borrower +
  // recommend loop). When present, the Draft outreach tile shows the
  // WarmingUpBlock instead of the template fallback — only after the
  // retries exhaust does the UI fall through to `defaultDraft` + the
  // muted "Default template used" note. 2026-04-23 UX fix.
  const [draftWarming, setDraftWarming] = useState<WarmingUpState | null>(null);
  const {
    setApproval,
    approvals,
    lender,
    lastBorrowerId,
    setLastBorrowerId,
    saveLead,
    isLeadSaved,
    savedDrafts,
    saveDraft,
  } = useApp();
  const approval = id ? approvals[id] : undefined;
  const savedDraftBody = id ? savedDrafts[id]?.body : undefined;

  useEffect(() => {
    if (id) setLastBorrowerId(id);
  }, [id, setLastBorrowerId]);

  useEffect(() => {
    if (!id) return;
    // AbortController cancels all per-borrower fetches when the id
    // changes or the route unmounts. Round-2 hole-finder #10/#11,
    // 2026-04-23. Warming-up handling (2026-04-23 UX fix) retries the
    // borrower + recommend pair up to 6 times at 5s intervals when the
    // warehouse is cold-starting; non-retryable errors fall through to
    // the loadError path.
    const ctrl = new AbortController();
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    const MAX_ATTEMPTS = 6;
    const INTERVAL_MS = 5000;

    // SWR: if we have a cached snapshot that's still fresh, hydrate
    // from it immediately so navigating back to a borrower is instant.
    // We still refetch in the background so the data stays live.
    const cached = readBorrowerCache(id);
    if (cached && reloadToken === 0) {
      const cachedDraftBody =
        savedDraftBody && savedDraftBody.trim().length > 0
          ? savedDraftBody
          : cached.draftBody;
      setB(cached.borrower);
      setRec(cached.recommendation);
      setLoadError(null);
      setWarmingUp(null);
      if (cachedDraftBody && cachedDraftBody.trim().length > 0) {
        setDraftBody(cachedDraftBody);
        setDraftLoaded(true);
      } else {
        setDraftBody('');
        setDraftLoaded(false);
      }
    } else {
      setB(null);
      setRec(null);
      setLoadError(null);
      setWarmingUp(null);
      setDraftBody('');
      setDraftLoaded(false);
    }

    const runAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const [borrower, recommendation] = await Promise.all([
          api.borrower(id, ctrl.signal),
          api.recommendOffer(id, ctrl.signal),
        ]);
        if (cancelled) return;
        setB(borrower);
        setRec(recommendation);
        setWarmingUp(null);
        setLoadError(null);
        const prev = BORROWER_CACHE.get(id);
        BORROWER_CACHE.set(id, {
          borrower,
          recommendation,
          draftBody: prev?.draftBody ?? null,
          fetched: Date.now(),
        });
      } catch (err: unknown) {
        if (cancelled || isAbortError(err)) return;
        if (isWarmingUpError(err) && attempt < MAX_ATTEMPTS) {
          setWarmingUp({
            dependency: err.dependency,
            label: `${dependencyLabel(err.dependency)} warming up`,
            attempt: attempt + 1,
            maxAttempts: MAX_ATTEMPTS,
            correlationId: err.correlationId,
          });
          setLoadError(null);
          timeoutId = setTimeout(() => {
            void runAttempt(attempt + 1);
          }, INTERVAL_MS);
          return;
        }
        setWarmingUp(null);
        setLoadError(
          err instanceof Error
            ? `Couldn't load borrower or offer: ${err.message}`
            : "Couldn't load borrower or offer.",
        );
      }
    };

    void runAttempt(1);

    // Fetch the backend-generated draft in parallel via a mirror of
    // the main borrower+recommend retry loop. On a 503 retryable
    // (warehouse warming), we show WarmingUpBlock inside the Draft
    // outreach tile and auto-retry up to MAX_ATTEMPTS. Only after the
    // retry budget is exhausted do we fall through to the hardcoded
    // template + the muted "Default template used" note — this
    // preserves the final-fallback UX while preventing the silent
    // template swap on cold boot. 2026-04-23 UX fix.
    let draftTimeoutId: ReturnType<typeof setTimeout> | null = null;
    setDraftWarming(null);

    const runDraftAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const draft = await api.draftOutreach(id, 'email', ctrl.signal);
        if (cancelled) return;
        setDraftWarming(null);
        if (draft?.body && draft.body.trim().length > 0) {
          const body =
            savedDraftBody && savedDraftBody.trim().length > 0
              ? savedDraftBody
              : draft.body;
          setDraftBody(body);
          setDraftLoaded(true);
          const prev = BORROWER_CACHE.get(id);
          if (prev) {
            BORROWER_CACHE.set(id, {
              ...prev,
              draftBody: body,
              fetched: Date.now(),
            });
          }
        }
      } catch (err: unknown) {
        if (cancelled || isAbortError(err)) return;
        if (isWarmingUpError(err) && attempt < MAX_ATTEMPTS) {
          setDraftWarming({
            dependency: err.dependency,
            label: `${dependencyLabel(err.dependency)} warming up`,
            attempt: attempt + 1,
            maxAttempts: MAX_ATTEMPTS,
            correlationId: err.correlationId,
          });
          draftTimeoutId = setTimeout(() => {
            void runDraftAttempt(attempt + 1);
          }, INTERVAL_MS);
          return;
        }
        // Retries exhausted or non-warming-up error — fall through to
        // the hardcoded template. draftLoaded stays false so the render
        // path shows the muted "Default template used" note. This is
        // the final-fallback UX called out in CLAUDE.md.
        setDraftWarming(null);
      }
    };

    void runDraftAttempt(1);

    return () => {
      cancelled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
      if (draftTimeoutId !== null) clearTimeout(draftTimeoutId);
      ctrl.abort();
    };
  }, [id, reloadToken, savedDraftBody]);

  // Offer Orchestrator is a per-borrower action page; without an id
  // render an empty-state landing page so the tab click isn't a silent
  // redirect. 2026-04-23 UX fix.
  if (!id && lastBorrowerId) {
    return <Navigate to={`/offer-orchestrator/${lastBorrowerId}`} replace />;
  }

  if (!id) {
    return (
      <PageShell
        eyebrow="Offer Orchestrator"
        title="Choose a borrower to compose an offer"
        lede="Offer Orchestrator drafts a tailored recommendation — HELOC / Cash-Out / Rate-Term Refi / Retention — from the borrower's score + equity + rate spread, then routes through human approval before any outreach goes out. Pick a borrower to begin."
        heroRight={
          <Link className="btn btn--primary" to="/lead-queue">
            Browse lead queue
            <Icon name="chevright" size={14} />
          </Link>
        }
      >
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="bolt" size={14} className="icon-accent" />
            <div className="h-4">What you'll see</div>
          </div>
          <div className="surface__body surface__body--stack-sm">
            <div className="chip-row">
              <Chip variant="neutral" icon="bolt">Primary offer</Chip>
              <Chip variant="neutral" icon="doc">Considered alternatives</Chip>
              <Chip variant="neutral" icon="shield">Thresholds applied</Chip>
              <Chip variant="neutral" icon="check">Human approval gate</Chip>
            </div>
            <p className="body muted flush">
              Every draft writes an audit row before it enters the outreach
              queue. No outreach sends automatically.
            </p>
          </div>
        </div>
      </PageShell>
    );
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
  const draftText = draftLoaded ? draftBody : defaultDraft;
  const savedDraft = id ? savedDrafts[id] : undefined;
  const draftIsSaved = Boolean(savedDraft && savedDraft.body === draftText);
  const leadIsSaved = b ? isLeadSaved(b.borrower_id) : false;
  const saveCurrentLead = () => {
    if (!b) return;
    saveLead({
      borrower_id: b.borrower_id,
      city: b.city,
      state: b.state,
      zip: b.zip,
      recommended_offer: rec?.product_label ?? b.recommended_offer,
      opportunity_score: b.opportunity_score,
      confidence: b.confidence,
    });
  };
  const saveCurrentDraft = () => {
    if (!id || draftText.trim().length === 0) return;
    saveDraft({
      borrower_id: id,
      offer_code: rec?.offer_code ?? b?.recommended_offer ?? null,
      channel: 'email',
      body: draftText,
    });
  };

  const onApprove = async () => {
    if (approving) return;
    setApproveError(null);
    setApproving(true);
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
      const draft_body = draftText || null;
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
    } finally {
      setApproving(false);
    }
  };

  const onReject = async () => {
    if (approving) return;
    setApproveError(null);
    setApproving(true);
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
    } finally {
      setApproving(false);
    }
  };

  if (warmingUp) {
    return (
      <PageShell
        eyebrow={warmingUp.label}
        title={`Loading ${id}…`}
        lede="Databricks SQL warehouses auto-suspend when idle. It takes ~30 seconds to warm up. Retrying automatically…"
      >
        <WarmingUpBlock state={warmingUp} title={`Loading offer for ${id}`} />
      </PageShell>
    );
  }

  if (loadError) {
    return (
      <PageShell
        eyebrow="Offer & Outreach"
        title={`Couldn't load ${id}`}
        lede={loadError}
      >
        <div className="surface">
          <div className="surface__body surface__body--inline">
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
            <Icon name="bolt" size={14} className="icon-accent" />
            <div className="h-4">Primary offer</div>
          </div>
          <div className="surface__body">
            <div className="split-row">
              <div className="offer-title offer-title--large">
                {productLabel}
              </div>
              {b && <ScoreBadge value={b.opportunity_score} />}
            </div>
            {rec ? (
              <p className="body mt-2">
                {rec.rationale ?? b?.why_now}
              </p>
            ) : (
              <div className="stack-sm mt-2">
                <Skeleton width="100%" height={14} rounded="sm" />
                <Skeleton width="92%" height={14} rounded="sm" />
                <Skeleton width="78%" height={14} rounded="sm" />
              </div>
            )}
            <div className="chip-row mt-3">
              <span className="muted fs-11">Sources:</span>
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
            {b && (
              <div className="chip-row mt-3">
                <Button
                  variant={leadIsSaved ? 'ghost' : 'default'}
                  size="sm"
                  icon={leadIsSaved ? 'check' : 'tag'}
                  onClick={saveCurrentLead}
                  aria-label={`${leadIsSaved ? 'Saved' : 'Save'} borrower ${b.borrower_id}`}
                >
                  {leadIsSaved ? 'Lead saved' : 'Save lead'}
                </Button>
              </div>
            )}
          </div>
        </div>
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="doc" size={14} className="icon-accent" />
            <div className="h-4">Draft outreach · review only</div>
          </div>
          <div className="surface__body">
            {draftWarming && (
              <div className="mb-3">
                <WarmingUpBlock
                  state={draftWarming}
                  title="Offer draft warming up"
                  compact
                />
              </div>
            )}
            {!draftLoaded && !draftWarming && b && (
              <div
                data-testid="draft-fallback-note"
                className="muted fs-11 mb-2"
              >
                Default template used — offer-draft endpoint unavailable.
              </div>
            )}
            <textarea
              key={b?.borrower_id ?? 'empty'}
              aria-label="Outreach draft — review only"
              value={draftText}
              onChange={(e) => {
                // Hydrate draftBody on first edit when the backend
                // draft never loaded, so subsequent edits accumulate
                // on top of the default template rather than reverting.
                if (!draftLoaded) setDraftLoaded(true);
                setDraftBody(e.target.value);
              }}
              data-testid="outreach-draft"
              className="route-textarea route-textarea--outreach"
            />
            <div className="muted fs-11 mt-2">
              First-name placeholder fills from CRM at send time. Phone number is a lender default — replace with the campaign&apos;s tracking number.
            </div>
            <div className="chip-row mt-3">
              <Chip variant="neutral" icon="shield">Email channel</Chip>
              <Chip variant="neutral">LO call follow-up within 5 days</Chip>
              <Button
                variant={draftIsSaved ? 'ghost' : 'default'}
                size="sm"
                icon={draftIsSaved ? 'check' : 'doc'}
                onClick={saveCurrentDraft}
                disabled={!id || draftText.trim().length === 0}
                aria-label={`Save outreach draft for ${id ?? 'borrower'}`}
              >
                {draftIsSaved ? 'Draft saved' : 'Save draft'}
              </Button>
            </div>
          </div>
        </div>
      </div>

      <Reveal className="layoutA-grid mt-grid">
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="doc" size={14} className="icon-accent" />
            <div>
              <div className="eyebrow">Considered alternatives</div>
              <div className="h-4 mt-1">
                {rec ? `${rec.alternatives.length} other product${rec.alternatives.length === 1 ? '' : 's'} ruled out` : 'Loading…'}
              </div>
            </div>
          </div>
          <div className="surface__body">
            {rec && rec.alternatives.length === 0 && (
              <p className="muted body flush">
                No alternatives considered — no trigger fires.
              </p>
            )}
            {rec && rec.alternatives.length > 0 && (
              <div className="stack-md">
                {rec.alternatives.map((alt) => (
                  <div
                    key={alt.offer_code}
                    className="alt-card"
                  >
                    <div className="split-row">
                      <div className="fw-600 text-1">{alt.product_label}</div>
                      <Chip variant="neutral" className="mono">{alt.offer_code}</Chip>
                    </div>
                    <p className="body muted flush">{alt.reason_not_chosen}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="surface">
          <div className="surface__hdr">
            <Icon name="shield" size={14} className="icon-accent" />
            <div>
              <div className="eyebrow">Thresholds applied</div>
              <div className="h-4 mt-1">Admin config at decision time</div>
            </div>
          </div>
          <div className="surface__body">
            {rec ? (
              <div className="threshold-grid">
                {Object.entries(rec.thresholds_applied).map(([k, v]) => (
                  <div key={k} className="contents">
                    <span className="muted body">{humanizeThresholdKey(k)}</span>
                    <span className="mono fs-13 text-1">{v}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted body flush">Loading thresholds…</p>
            )}
          </div>
        </div>
      </Reveal>

      <div className="mt-grid">
        <ApprovalBanner
          text={`${b ? `Borrower ${b.borrower_id}` : 'Borrower'} pending review. Approve writes an audit event and releases the draft into the outreach queue.`}
          onApprove={() => void onApprove()}
          onReject={() => void onReject()}
          disabled={approval === 'approved' || approval === 'rejected'}
          isSubmitting={approving}
        />
      </div>

      {approval === 'approved' && (
        <div className="surface mt-grid">
          <div className="surface__body surface__body--inline">
            <span className="burst inline-flex">
              <Chip variant="success" icon="check">Approved · released to outreach queue</Chip>
            </span>
            {auditId && <span className="mono muted fs-11">audit: {auditId}</span>}
          </div>
        </div>
      )}
      {approval === 'rejected' && (
        <div className="surface mt-grid">
          <div className="surface__body">
            <Chip variant="danger" icon="cross">Rejected</Chip>
          </div>
        </div>
      )}
      {approveError && (
        <div
          className="surface surface--danger mt-grid"
          role="alert"
        >
          <div className="surface__body text-danger">
            {approveError}
          </div>
        </div>
      )}
    </PageShell>
  );
}
