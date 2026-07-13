import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api, ApiError, isAbortError, isWarmingUpError, dependencyLabel } from '../lib/api';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import type { ApprovalStatus, Borrower360 as Borrower360Type, BorrowerLifecycle, OfferRecommendation, SalesTeamMember } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { BorrowerOfferPreviewMock } from '../components/mortgage/BorrowerOfferPreviewMock';
import { TopLeadsQuickPick } from '../components/mortgage/TopLeadsQuickPick';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Button, Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { useApp } from '../components/AppContext';
import { ActivationLoopPanel } from '../components/activation/ActivationLoopPanel';
import { invalidateOperationalQueries } from '../lib/queryKeys';
import { offerDisplayLabel } from '../lib/offerLanguage';
import { BORROWER_CACHE, clearBorrowerCache, readBorrowerCache } from './offer-orchestrator.cache';
import { DEFAULT_REJECT_REASON, type OutreachChannel, type RejectReasonCode } from './offer-orchestrator.constants';
import {
  OfferDetailsRows,
  OfferOrchestratorEmptyHero,
  OfferOrchestratorEmptyState,
  OfferReviewGrid,
  RejectRationalePanel,
} from './offer-orchestrator.panels';

/**
 * Offer Orchestrator — convert the borrower intelligence into a drafted
 * message (never auto-sent) that a human approves before Lakeflow posts to
 * marketing. Approval state flows into AppContext so the Lead Queue chip and
 * audit log stay in sync.
 */

export function resolveOfferApprovalStatus(
  local: ApprovalStatus | undefined,
  lifecycleStatus: ApprovalStatus | undefined,
  borrowerStatus: ApprovalStatus | undefined,
): ApprovalStatus | undefined {
  const durable = lifecycleStatus ?? borrowerStatus;
  if (durable === 'approved' || durable === 'rejected' || durable === 'hold') {
    return local ?? durable;
  }
  return local;
}

export default function OfferOrchestrator() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const [b, setB] = useState<Borrower360Type | null>(null);
  const [rec, setRec] = useState<OfferRecommendation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadErrorStatus, setLoadErrorStatus] = useState<number | null>(null);
  // Cold-start warming-up state for the borrower + recommend fetch pair.
  // Shape matches WarmingUpBlock's contract. Non-null means we're in a
  // 503 retry loop and the UI should show "Warehouse warming up
  // (attempt N of 6)…" instead of the red error banner.
  const [warmingUp, setWarmingUp] = useState<WarmingUpState | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [auditId, setAuditId] = useState<string | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [lifecycle, setLifecycle] = useState<BorrowerLifecycle | null>(null);
  // R5-11 (2026-04-23): in-flight flag forwarded to ApprovalBanner so
  // the buttons disable while a POST is pending. Prevents a double
  // click from writing two audit rows.
  const [approving, setApproving] = useState<boolean>(false);
  // Reload token re-runs the borrower + recommend + draft fetches.
  // Hole-finder finding #1, 2026-04-23.
  const [reloadToken, setReloadToken] = useState<number>(0);
  // 2026-04-22: the draft textarea is controlled and hydrated from
  // /api/outreach/draft so edits persist through approve.
  // 2026-05-08: fail closed if the backend draft endpoint is unavailable;
  // the UI must not generate or approve local outreach copy.
  const [draftBody, setDraftBody] = useState<string>('');
  const [draftSubject, setDraftSubject] = useState<string>('');
  const [draftChannel, setDraftChannel] = useState<OutreachChannel>('email');
  const [draftLoaded, setDraftLoaded] = useState<boolean>(false);
  const [draftPending, setDraftPending] = useState<boolean>(true);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftBaselineBody, setDraftBaselineBody] = useState('');
  const [draftBaselineSubject, setDraftBaselineSubject] = useState('');
  const [draftDisclosureVersion, setDraftDisclosureVersion] = useState<string | null>(null);
  const [draftDisclosureState, setDraftDisclosureState] = useState<string | null>(null);
  const [draftGeneratorLabel, setDraftGeneratorLabel] = useState<string | null>(null);
  const [draftGenerationMode, setDraftGenerationMode] = useState<'supervisor' | 'governed_fallback' | null>(null);
  const [draftStrategy, setDraftStrategy] = useState<string | null>(null);
  const [draftEvidence, setDraftEvidence] = useState<string[]>([]);
  const [draftEvidenceAssets, setDraftEvidenceAssets] = useState<string[]>([]);
  // Cold-start warming-up state for the draftOutreach fetch. Non-null =
  // the draft endpoint is in a 503 retry loop (mirrors the borrower +
  // recommend loop). When present, the Draft outreach tile shows the
  // WarmingUpBlock; after retries exhaust the approval path remains
  // disabled until a governed draft loads.
  const [draftWarming, setDraftWarming] = useState<WarmingUpState | null>(null);
  const [rejectReviewOpen, setRejectReviewOpen] = useState(false);
  const [rejectReasonCode, setRejectReasonCode] = useState<RejectReasonCode>(DEFAULT_REJECT_REASON);
  // Feature C: optional loan-officer assignment + follow-up reminder captured
  // at approval time and persisted on the approval row.
  const [salesTeam, setSalesTeam] = useState<SalesTeamMember[]>([]);
  const [assignedTo, setAssignedTo] = useState<string>('');
  const [followUpDays, setFollowUpDays] = useState<number>(0); // 0 = no reminder
  const [routingConfirm, setRoutingConfirm] = useState<{ email: string | null; followUpAt: string | null } | null>(null);
  // Auto-offer Module 1 (prototype): preview the borrower-facing offer experience.
  const [borrowerPreviewOpen, setBorrowerPreviewOpen] = useState(false);

  // Load the loan-officer roster for the assignment picker (active LOs +
  // managers). Best-effort — the control degrades to "Unassigned" only if the
  // roster can't load; approval itself never depends on it. Top-level hook
  // (before any early return) so hook order is stable.
  useEffect(() => {
    let cancelled = false;
    api
      .salesTeam()
      .then((members) => {
        if (cancelled) return;
        setSalesTeam(
          members.filter((m) => m.active && (m.role === 'loan_officer' || m.role === 'sales_manager')),
        );
      })
      .catch(() => {
        /* roster unavailable → picker stays "Unassigned"; non-fatal */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const [rejectRationale, setRejectRationale] = useState('');
  const {
    setApproval,
    approvals,
    lastBorrowerId,
    setLastBorrowerId,
    saveLead,
    isLeadSaved,
    savedDrafts,
    saveDraft,
    removeSavedDraft,
  } = useApp();
  const approval = id ? approvals[id] : undefined;
  const savedDraftKey = id ? `${id}::${draftChannel}` : null;
  const savedDraftBody = savedDraftKey ? savedDrafts[savedDraftKey]?.body : undefined;
  const savedDraftSubject = savedDraftKey ? savedDrafts[savedDraftKey]?.subject : undefined;

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
          : cached.draftChannel === draftChannel
            ? cached.draftBody
            : null;
      const cachedDraftSubject =
        savedDraftSubject && savedDraftSubject.trim().length > 0
          ? savedDraftSubject
          : cached.draftChannel === draftChannel
            ? cached.draftSubject
            : null;
      setB(cached.borrower);
      setRec(cached.recommendation);
      setLifecycle(null);
      setApprovalId(null);
      setAuditId(null);
      setLoadError(null);
      setLoadErrorStatus(null);
      setWarmingUp(null);
      if (cachedDraftBody && cachedDraftBody.trim().length > 0) {
        setDraftBody(cachedDraftBody);
        setDraftSubject(cachedDraftSubject ?? '');
        setDraftLoaded(true);
        setDraftPending(false);
        setDraftBaselineBody(cachedDraftBody);
        setDraftBaselineSubject(cachedDraftSubject ?? '');
      } else {
        setDraftBody('');
        setDraftSubject('');
        setDraftLoaded(false);
        setDraftPending(true);
        setDraftBaselineBody('');
        setDraftBaselineSubject('');
        setDraftDisclosureVersion(null);
        setDraftDisclosureState(null);
      }
    } else {
      setB(null);
      setRec(null);
      setLifecycle(null);
      setApprovalId(null);
      setAuditId(null);
      setLoadError(null);
      setLoadErrorStatus(null);
      setWarmingUp(null);
      setDraftBody('');
      setDraftSubject('');
      setDraftLoaded(false);
      setDraftPending(true);
      setDraftBaselineBody('');
      setDraftBaselineSubject('');
    }

    const runAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const [borrower, recommendation, loadedLifecycle] = await Promise.all([
          api.borrower(id, ctrl.signal),
          api.recommendOffer(id, ctrl.signal),
          api.borrowerLifecycle(id, ctrl.signal).catch(() => null),
        ]);
        if (cancelled) return;
        setB(borrower);
        setRec(recommendation);
        setLifecycle(loadedLifecycle);
        if (loadedLifecycle?.approval_id) {
          setApprovalId(loadedLifecycle.approval_id);
        }
        setWarmingUp(null);
        setLoadError(null);
        setLoadErrorStatus(null);
        const prev = BORROWER_CACHE.get(id);
        BORROWER_CACHE.set(id, {
          borrower,
          recommendation,
          draftSubject: prev?.draftChannel === draftChannel ? (prev?.draftSubject ?? null) : null,
          draftBody: prev?.draftChannel === draftChannel ? (prev?.draftBody ?? null) : null,
          draftChannel: prev?.draftChannel === draftChannel ? draftChannel : null,
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
          setLoadErrorStatus(null);
          timeoutId = setTimeout(() => {
            void runAttempt(attempt + 1);
          }, INTERVAL_MS);
          return;
        }
        setWarmingUp(null);
        setLoadErrorStatus(err instanceof ApiError ? err.status : null);
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
    // (warehouse warming), show WarmingUpBlock inside the Draft
    // outreach tile and auto-retry up to MAX_ATTEMPTS. If the retry
    // budget exhausts, approval stays blocked; there is no local
    // outreach copy fallback.
    let draftTimeoutId: ReturnType<typeof setTimeout> | null = null;
    setDraftWarming(null);
    setDraftError(null);

    const runDraftAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const draft = await api.draftOutreach(id, draftChannel, ctrl.signal);
        if (cancelled) return;
        setDraftWarming(null);
        if (draft?.body && draft.body.trim().length > 0) {
          const body =
            savedDraftBody && savedDraftBody.trim().length > 0
              ? savedDraftBody
              : draft.body;
          const subject =
            savedDraftSubject && savedDraftSubject.trim().length > 0
              ? savedDraftSubject
              : (draft.subject ?? '');
          setDraftBody(body);
          setDraftSubject(subject);
          setDraftLoaded(true);
          setDraftPending(false);
          setDraftBaselineBody(body);
          setDraftBaselineSubject(subject);
          setDraftDisclosureVersion(draft.disclosure_version);
          setDraftDisclosureState(draft.disclosure_state);
          setDraftGeneratorLabel(draft.generator_label);
          setDraftGenerationMode(draft.generation_mode);
          setDraftStrategy(draft.strategy_summary);
          setDraftEvidence(draft.evidence_summary);
          setDraftEvidenceAssets(draft.evidence_assets);
          const prev = BORROWER_CACHE.get(id);
          if (prev) {
            BORROWER_CACHE.set(id, {
              ...prev,
              draftSubject: subject || null,
              draftBody: body,
              draftChannel,
              fetched: Date.now(),
            });
          }
        } else {
          setDraftLoaded(false);
          setDraftPending(false);
          setDraftSubject('');
          setDraftDisclosureVersion(null);
          setDraftDisclosureState(null);
          setDraftGeneratorLabel(null);
          setDraftGenerationMode(null);
          setDraftStrategy(null);
          setDraftEvidence([]);
          setDraftEvidenceAssets([]);
          setDraftError('Offer draft endpoint returned an empty draft. Approval is disabled until an audited draft loads.');
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
        setDraftWarming(null);
        setDraftLoaded(false);
        setDraftPending(false);
        setDraftDisclosureVersion(null);
        setDraftDisclosureState(null);
        setDraftGeneratorLabel(null);
        setDraftGenerationMode(null);
        setDraftStrategy(null);
        setDraftEvidence([]);
        setDraftEvidenceAssets([]);
        setDraftError(
          err instanceof Error
            ? `Offer draft unavailable: ${err.message}`
            : 'Offer draft unavailable.',
        );
      }
    };

    void runDraftAttempt(1);

    return () => {
      cancelled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
      if (draftTimeoutId !== null) clearTimeout(draftTimeoutId);
      ctrl.abort();
    };
  }, [draftChannel, id, reloadToken, savedDraftBody, savedDraftSubject]);

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
        lede="Offer Orchestrator explains the selected offer path, considered alternatives, and borrower-facing draft before any outreach can be approved. Pick a borrower to begin."
        heroRight={<OfferOrchestratorEmptyHero to="/lead-queue" />}
      >
        <OfferOrchestratorEmptyState />
        <TopLeadsQuickPick basePath="/offer-orchestrator" />
      </PageShell>
    );
  }

  const productLabel = offerDisplayLabel(
    rec?.offer_code ?? b?.recommended_offer_code,
    rec?.product_label ?? b?.recommended_offer ?? '...',
  );
  const effectiveApproval = resolveOfferApprovalStatus(
    approval,
    lifecycle?.approval_status,
    b?.approval_status,
  );
  const draftText = draftLoaded ? draftBody : '';
  const subjectReady = draftChannel === 'sms' || draftSubject.trim().length > 0;
  const draftReady = draftLoaded && subjectReady && draftText.trim().length > 0;
  const draftDirty = draftLoaded && (
    draftBody !== draftBaselineBody
    || (draftChannel !== 'sms' && draftSubject !== draftBaselineSubject)
  );
  const savedDraft = savedDraftKey ? savedDrafts[savedDraftKey] : undefined;
  const draftIsSaved = Boolean(
    savedDraft
      && savedDraft.body === draftText
      && (savedDraft.subject ?? '') === (draftChannel === 'sms' ? '' : draftSubject),
  );
  const leadIsSaved = b ? isLeadSaved(b.borrower_id) : false;
  const saveCurrentLead = () => {
    if (!b) return;
    saveLead({
      borrower_id: b.borrower_id,
      city: b.city,
      state: b.state,
      zip: b.zip,
      recommended_offer: productLabel,
      opportunity_score: b.opportunity_score,
      confidence: b.confidence,
    });
  };
  const saveCurrentDraft = () => {
    if (!id || !draftReady) return;
    saveDraft({
      borrower_id: id,
      offer_code: rec?.offer_code ?? b?.recommended_offer_code ?? null,
      channel: draftChannel,
      subject: draftChannel === 'sms' ? null : draftSubject,
      body: draftText,
    });
    setDraftBaselineBody(draftText);
    setDraftBaselineSubject(draftChannel === 'sms' ? '' : draftSubject);
  };
  const resetCurrentDraft = () => {
    if (!id) return;
    removeSavedDraft(id, draftChannel);
    const cached = BORROWER_CACHE.get(id);
    if (cached) {
      BORROWER_CACHE.set(id, { ...cached, draftSubject: null, draftBody: null, fetched: 0 });
    }
    setDraftBody('');
    setDraftSubject('');
    setDraftLoaded(false);
    setDraftPending(true);
    setDraftBaselineBody('');
    setDraftBaselineSubject('');
    setDraftDisclosureVersion(null);
    setDraftDisclosureState(null);
    setDraftGeneratorLabel(null);
    setDraftGenerationMode(null);
    setDraftStrategy(null);
    setDraftEvidence([]);
    setDraftEvidenceAssets([]);
    setReloadToken((n) => n + 1);
  };

  const regenerateDraft = () => {
    if (!id || approving) return;
    const cached = BORROWER_CACHE.get(id);
    if (cached) BORROWER_CACHE.set(id, { ...cached, draftSubject: null, draftBody: null, fetched: 0 });
    setDraftLoaded(false);
    setDraftPending(true);
    setDraftBody('');
    setDraftSubject('');
    setDraftBaselineBody('');
    setDraftBaselineSubject('');
    setDraftError(null);
    setDraftGeneratorLabel(null);
    setDraftGenerationMode(null);
    setDraftStrategy(null);
    setDraftEvidence([]);
    setDraftEvidenceAssets([]);
    setReloadToken((n) => n + 1);
  };

  const onApprove = async () => {
    if (approving) return;
    setApproveError(null);
    if (!draftReady) {
      setApproveError('Approval is disabled until the audited outreach draft loads from the backend.');
      return;
    }
    setApproving(true);
    try {
      // Forward the chosen offer_code + evidence_ids so the audit row
      // captures what the approver actually saw — not just the borrower
      // id. Falls back to the borrower's recommended_offer when the
      // recommendation hasn't hydrated yet.
      const offer_code = rec?.offer_code ?? b?.recommended_offer_code ?? null;
      const evidence_ids = rec?.evidence_ids ?? b?.evidence_ids ?? [];
      const draft_body = draftText;
      const draft_subject = draftChannel === 'sms' ? null : draftSubject;
      const res = await api.approve(id, {
        offer_code,
        evidence_ids,
        draft_subject,
        draft_body,
        channel: draftChannel,
        assigned_to_email: assignedTo || null,
        follow_up_in_days: followUpDays > 0 ? followUpDays : null,
      });
      if (res.approved) {
        setApproval(id, 'approved');
        setAuditId(res.audit_event_id ?? null);
        setApprovalId(res.approval_id ?? null);
        setRoutingConfirm({
          email: res.assigned_to_email ?? (assignedTo || null),
          followUpAt: res.follow_up_at ?? null,
        });
        clearBorrowerCache(id);
        void invalidateOperationalQueries(queryClient);
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
    if (!rejectReviewOpen) {
      setRejectReviewOpen(true);
      return;
    }
    if (rejectReasonCode === 'other_with_text' && rejectRationale.trim().length === 0) {
      setApproveError('Rejection reason "Other" requires a rationale note.');
      return;
    }
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
      const offer_code = rec?.offer_code ?? b?.recommended_offer_code ?? null;
      const evidence_ids = rec?.evidence_ids ?? b?.evidence_ids ?? [];
      const res = await api.reject(id, {
        offer_code,
        evidence_ids,
        channel: draftChannel,
        rationale_code: rejectReasonCode,
        rationale: rejectRationale.trim() || null,
      });
      if (res.rejected) {
        setApproval(id, 'rejected');
        setAuditId(res.audit_event_id ?? null);
        clearBorrowerCache(id);
        void invalidateOperationalQueries(queryClient);
        setRejectReviewOpen(false);
        setRejectReasonCode(DEFAULT_REJECT_REASON);
        setRejectRationale('');
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
    const notFound = loadErrorStatus === 404;
    return (
      <PageShell
        eyebrow="Offer & Outreach"
        title={notFound ? `Borrower ${id} not found` : `Couldn't load ${id}`}
        lede={notFound ? `Borrower ${id} was not found. Check the ID, use search, or return to the lead queue.` : loadError}
      >
        <div className="surface">
          <div className="surface__body surface__body--inline">
            <Chip variant={notFound ? 'warning' : 'danger'} icon={notFound ? 'search' : 'cross'}>
              {notFound ? 'Not found' : 'Backend unavailable'}
            </Chip>
            {!notFound && (
              <button
                type="button"
                className="btn"
                onClick={() => setReloadToken((n) => n + 1)}
                aria-label="Retry loading borrower and offer"
              >
                Retry
              </button>
            )}
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
      lede="Review the selected offer path, alternatives considered, thresholds applied, and borrower-facing draft. Approve to place the decision in the governed internal queue; reject to drop the borrower."
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
              disabled={!rec || !draftReady || effectiveApproval === 'approved'}
              aria-label={
                effectiveApproval === 'approved'
                  ? `Borrower ${b.borrower_id} already approved`
                  : `Approve borrower ${b.borrower_id}`
              }
              data-testid="hero-approve"
            >
              {effectiveApproval === 'approved' ? 'Approved' : 'Approve'}
            </Button>
            {/* Auto-offer Module 1 prototype: show the borrower-facing offer
                experience (the "click yes" vision). Clearly a mock. */}
            <Button
              variant="ghost"
              size="sm"
              icon="user"
              onClick={() => setBorrowerPreviewOpen(true)}
              data-testid="preview-borrower-offer"
            >
              Preview borrower view
            </Button>
          </>
        )
      }
    >
      {borrowerPreviewOpen && b && (
        <BorrowerOfferPreviewMock borrower={b} onClose={() => setBorrowerPreviewOpen(false)} />
      )}
      {rejectReviewOpen && (
        <RejectRationalePanel
          reasonCode={rejectReasonCode}
          rationale={rejectRationale}
          onReasonChange={setRejectReasonCode}
          onRationaleChange={setRejectRationale}
          onCancel={() => {
            setRejectReviewOpen(false);
            setRejectRationale('');
            setRejectReasonCode(DEFAULT_REJECT_REASON);
          }}
          onSubmit={() => void onReject()}
        />
      )}
      <OfferReviewGrid
        borrower={b}
        recommendation={rec}
        productLabel={productLabel}
        leadIsSaved={leadIsSaved}
        saveCurrentLead={saveCurrentLead}
        draftWarming={draftWarming}
        draftPending={draftPending}
        draftLoaded={draftLoaded}
        draftError={draftError}
        draftSubject={draftSubject}
        onDraftSubjectChange={setDraftSubject}
        draftText={draftText}
        onDraftChange={setDraftBody}
        draftChannel={draftChannel}
        draftDirty={draftDirty}
        onDraftChannelChange={(channel) => {
          setDraftChannel(channel);
          setDraftLoaded(false);
          setDraftPending(true);
          setDraftBody('');
          setDraftSubject('');
          setDraftBaselineBody('');
          setDraftBaselineSubject('');
          setDraftDisclosureVersion(null);
          setDraftDisclosureState(null);
          setDraftGeneratorLabel(null);
          setDraftGenerationMode(null);
          setDraftStrategy(null);
          setDraftEvidence([]);
          setDraftEvidenceAssets([]);
        }}
        approving={approving}
        draftDisclosureVersion={draftDisclosureVersion}
        draftDisclosureState={draftDisclosureState}
        draftGeneratorLabel={draftGeneratorLabel}
        draftGenerationMode={draftGenerationMode}
        draftStrategy={draftStrategy}
        draftEvidence={draftEvidence}
        draftEvidenceAssets={draftEvidenceAssets}
        regenerateDraft={regenerateDraft}
        draftIsSaved={draftIsSaved}
        saveCurrentDraft={saveCurrentDraft}
        savedDraftExists={Boolean(savedDraft)}
        resetCurrentDraft={resetCurrentDraft}
        draftReady={draftReady}
        borrowerId={id}
      />

      <OfferDetailsRows recommendation={rec} />

      {effectiveApproval !== 'approved' && effectiveApproval !== 'rejected' && (
        <>
          {/* Feature C: optional routing captured at approval time — which loan
              officer owns this outreach and when to follow up. Persisted on the
              approval row; approval never depends on it. */}
          <div className="outreach-routing mt-grid" data-testid="outreach-routing">
            <div className="outreach-routing__field">
              <label htmlFor="lo-assign" className="outreach-routing__label">Assign to loan officer</label>
              <select
                id="lo-assign"
                className="outreach-routing__select"
                value={assignedTo}
                onChange={(e) => setAssignedTo(e.target.value)}
                disabled={approving}
              >
                <option value="">Unassigned</option>
                {salesTeam.map((m) => (
                  <option key={m.email} value={m.email}>
                    {m.display_label}
                    {m.region ? ` · ${m.region}` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="outreach-routing__field">
              <label htmlFor="lo-followup" className="outreach-routing__label">Follow-up reminder</label>
              <select
                id="lo-followup"
                className="outreach-routing__select"
                value={followUpDays}
                onChange={(e) => setFollowUpDays(Number(e.target.value))}
                disabled={approving}
              >
                <option value={0}>None</option>
                <option value={3}>In 3 days</option>
                <option value={5}>In 5 days</option>
                <option value={7}>In 7 days</option>
                <option value={14}>In 14 days</option>
              </select>
            </div>
          </div>
          <div className="mt-grid">
            <ApprovalBanner
              text={`${b ? `Borrower ${b.borrower_id}` : 'Borrower'} pending review. Approve writes an audit event and places the decision in the governed internal queue.`}
              onApprove={() => void onApprove()}
              onReject={() => void onReject()}
              approveDisabled={!draftReady}
              isSubmitting={approving}
            />
          </div>
        </>
      )}
      {routingConfirm && (routingConfirm.email || routingConfirm.followUpAt) && (
        <div className="outreach-routing__confirm mt-grid" role="status" data-testid="routing-confirm">
          <Chip variant="success">
            {routingConfirm.email ? `Assigned to ${routingConfirm.email}` : 'Unassigned'}
            {routingConfirm.followUpAt
              ? ` · follow-up ${new Date(routingConfirm.followUpAt).toLocaleDateString(undefined, {
                  month: 'short',
                  day: 'numeric',
                })}`
              : ''}
          </Chip>
        </div>
      )}

      {effectiveApproval === 'approved' && (
        <>
          <div className="surface mt-grid">
            <div className="surface__body surface__body--inline">
              <span className="burst inline-flex">
                <Chip variant="success" icon="check">Approved · governed internal queue</Chip>
              </span>
              {auditId && <span className="mono muted fs-11">audit: {auditId}</span>}
              {approvalId && <span className="mono muted fs-11">approval: {approvalId}</span>}
            </div>
          </div>
          <ActivationLoopPanel
            borrowerId={b?.borrower_id ?? id}
            offerCode={rec?.offer_code ?? b?.recommended_offer_code ?? null}
            channel={draftChannel}
            approvalId={approvalId}
            approved
          />
        </>
      )}
      {effectiveApproval === 'rejected' && (
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
