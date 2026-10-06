import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation, useParams } from 'react-router';
import type { ApproveResult, RejectResult } from '../lib/apiTypes';
import {
  offerMutationKeys,
  useOfferApprove,
  useOfferDraftSave,
  useOfferReject,
  type OfferApproveBody,
  type OfferRejectBody,
} from '../lib/mutations/offer';
import { isDecisionPending } from '../lib/mutations/outreach';
import { intentFingerprint, useIntentRequestIds } from '../lib/mutations/requestIds';
import { queueHref, useQueueContext } from '../lib/queueContext';
import { queuePosition } from '../lib/queuePosition';
import { offerPath } from '../lib/routeMeta';
import { queryKeys } from '../lib/queryKeys';
import { usePresenterMode } from '../lib/presenterMode';
import { PageShell } from '../components/layout/PageShell';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';
import { QueuePager } from '../components/mortgage/QueuePager';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Button, Chip } from '../components/Primitives';
import { useApp } from '../components/AppContext';
import { useUnsavedGuard } from '../hooks/useUnsavedGuard';
import { approverGateReason } from '../components/mortgage/approverGate';
import { offerDisplayLabel } from '../lib/offerLanguage';
import type { OutreachChannel, RejectReasonCode } from './offer-orchestrator.constants';
import { useOfferSalesTeam } from './offer-orchestrator.sales-team';
import { OfferReviewGrid, RejectRationalePanel } from './offer-orchestrator.panels';
import { OfferActionBar } from './offer-orchestrator.action-bar';
import { useOfferDraft, useOfferSnapshot } from './offer-orchestrator.queries';
import { draftProofMatchesSnapshot, resolveOfferApprovalStatus } from './offer-orchestrator.snapshot';
import { OfferSnapshotReconciliation } from './offer-orchestrator.snapshot-status';
import { OfferDecisionOutcome } from './offer-orchestrator.decision';
import { OfferPriorDecisions } from './offer-orchestrator.priorDecisions';
import { announceApprovalRouting, offerUnsavedMessage } from './offer-orchestrator.feedback';
import {
  OfferLoadErrorRoute,
  OfferOrchestratorEmptyRoute,
  OfferWarmingRoute,
  useOfferCampaignBinding,
} from './offer-orchestrator.route-ui';

// The PROTOTYPE borrower view is a demo affordance (D-shell-deviations-e1):
// shown only in presenter mode, and its module is never downloaded otherwise.
// Loaded through useLazyModule, not React.lazy, so a stale chunk after a
// deploy renders nothing instead of reaching the route error boundary.
const PREVIEW_MOCK_CHUNK = lazyModule(() => import('../components/mortgage/BorrowerOfferPreviewMock'));

// Saved campaign variants are currently persisted as governed email copy.
// Keep campaign handoffs on that exact channel until the campaign contract
// carries and verifies additional stored channel variants.
const SAVED_CAMPAIGN_OUTREACH_CHANNELS: readonly OutreachChannel[] = ['email'];

/** "Couldn't write approval: …" / "Couldn't record rejection: …", verbatim from before the port. */
function writeErrorCopy(err: unknown, lead: string): string {
  return err instanceof Error ? `${lead}: ${err.message}` : `${lead}.`;
}

function draftSaveErrorCopy(err: unknown): string {
  return err instanceof Error ? `Couldn't save draft: ${err.message}` : "Couldn't save draft.";
}

export default function OfferOrchestrator() {
  const { id } = useParams();
  const location = useLocation();
  // The ranked Lead Queue this offer was opened from (masked ids only), for
  // the pager, the Next-in-queue step and the queue-aware back links.
  const queue = useQueueContext(location.state, id ?? null);
  const { campaignBinding, campaignBindingError } = useOfferCampaignBinding();
  const queryClient = useQueryClient();
  const [approveError, setApproveError] = useState<string | null>(null);
  // Counts this view's explicit re-reads (Regenerate / Retry draft, a saved
  // draft's reset, the load-error Retry): the old effect's reloadToken, kept
  // only so a decision's one-shot receipt reveal is tied to the load it was
  // made on.
  const [loadGeneration, setLoadGeneration] = useState(0);
  // The decision the user just made in THIS view (borrower + load
  // generation), with the audit / approval ids its write returned. The
  // Decision receipt's one-shot reveal keys off this, never off durable
  // approval_status, so a stale decision never celebrates again on load and
  // a reload of the same borrower does not replay it (audit motion-06; the
  // receipt replaced the .burst chip in wave 1).
  const [decidedHere, setDecidedHere] = useState<{
    id: string;
    generation: number;
    auditId: string | null;
    approvalId: string | null;
  } | null>(null);
  // Carryover #12: where the approval made here was routed, from its response.
  const [approvalRouting, setApprovalRouting] = useState<{ id: string; assignedTo: string | null; followUpAt: string | null } | null>(null);
  const [draftChannel, setDraftChannel] = useState<OutreachChannel>('email');
  const [rejectReviewOpen, setRejectReviewOpen] = useState(false);
  // No default reason (D-approval-flow-d item 13): every rationale_code in the
  // ledger is one a reviewer chose.
  const [rejectReasonCode, setRejectReasonCode] = useState<RejectReasonCode | ''>('');
  const salesTeam = useOfferSalesTeam();
  const [assignedTo, setAssignedTo] = useState<string>('');
  const [followUpDays, setFollowUpDays] = useState<number>(0); // 0 = no reminder
  const [borrowerPreviewOpen, setBorrowerPreviewOpen] = useState(false);
  const presenterMode = usePresenterMode();
  const previewChunk = useLazyModule(PREVIEW_MOCK_CHUNK, presenterMode && borrowerPreviewOpen);
  const BorrowerOfferPreviewMock = presenterMode && borrowerPreviewOpen ? previewChunk.module?.BorrowerOfferPreviewMock : undefined;
  const allowedDraftChannels = campaignBinding
    ? SAVED_CAMPAIGN_OUTREACH_CHANNELS
    : undefined;
  const activeDraftChannel = allowedDraftChannels?.[0] ?? draftChannel;

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
    canAccessAdmin,
    canApprove,
    actorEmail,
    sessionStatus,
    genieOpen,
  } = useApp();
  // Audit flow-02: gate the approve controls on the session's can_approve.
  const approverGate = approverGateReason(canApprove, sessionStatus);
  // Governed writes on the mutation layer (stack-09): pessimistic, one
  // request_id per intent, pending / failure read from mutation state.
  const approveMutation = useOfferApprove(queryClient);
  const rejectMutation = useOfferReject(queryClient);
  const draftSave = useOfferDraftSave(saveDraft);
  const requestIds = useIntentRequestIds();
  const approving = approveMutation.isPending || rejectMutation.isPending;
  const draftSavePending = draftSave.isPending;
  const draftSaveError = draftSave.error ? draftSaveErrorCopy(draftSave.error) : null;
  const approval = id ? approvals[id] : undefined;
  const savedDraftKey = id ? `${id}::${activeDraftChannel}` : null;
  // The route is the ONE observer of both reads (offer-orchestrator.queries):
  // every child below gets them as props.
  const snapshot = useOfferSnapshot(id, campaignBinding);
  const draft = useOfferDraft(id, activeDraftChannel, campaignBinding, campaignBindingError);
  const b = snapshot.data?.borrower ?? null;
  const rec = snapshot.data?.recommendation ?? null;
  const lifecycle = snapshot.data?.lifecycle ?? null;
  const snapshotReconciling = snapshot.reconciling;
  const draftLoaded = draft.loaded;
  const draftSubject = draft.subject;
  const draftProof = draft.proof;
  const decisionHere = decidedHere !== null && decidedHere.id === id && decidedHere.generation === loadGeneration
    ? decidedHere
    : null;
  const justDecided = decisionHere !== null;
  // A decision made here cites its own write's ids; otherwise the lifecycle
  // row's (a durable decision reads its receipt back without the reveal).
  const auditId = decisionHere ? decisionHere.auditId : lifecycle?.audit_event_id ?? null;
  const approvalId = decisionHere ? decisionHere.approvalId : lifecycle?.approval_id ?? null;
  /** An explicit re-read: this open's snapshot and a new audited draft. */
  const rereadSnapshotAndDraft = () => {
    snapshot.refetch();
    draft.reset();
  };

  useEffect(() => {
    if (id) setLastBorrowerId(id);
  }, [id, setLastBorrowerId]);

  // The draft is review-only since critic-02: its copy only ever comes from
  // the server, so there is no edited draft to guard.
  // Audit states-05: unsaved typed work asks before a route leave or tab close.
  const unsavedMessage = offerUnsavedMessage(false, rejectReviewOpen && rejectRationale.trim().length > 0);
  useUnsavedGuard(unsavedMessage !== null, unsavedMessage ?? undefined);

  if (!id && lastBorrowerId) {
    return <Navigate to={`/offer-orchestrator/${lastBorrowerId}`} replace />;
  }

  if (!id) {
    return <OfferOrchestratorEmptyRoute backTo={queueHref(queue)} />;
  }

  // shell-04 / flow-09: step offer to offer through the queue. The keys are
  // off while the reject rationale is open (the only typed work here) and,
  // with the buttons, while a decision is on the wire.
  const pager = (
    <QueuePager
      borrowerId={id}
      queue={queue}
      pathFor={offerPath}
      hotkeys={!rejectReviewOpen && !approving}
      disabled={approving}
    />
  );

  const productLabel = offerDisplayLabel(
    rec?.offer_code ?? b?.recommended_offer_code,
    rec?.product_label ?? b?.recommended_offer ?? '...',
  );
  const effectiveApproval = resolveOfferApprovalStatus(
    approval,
    lifecycle?.approval_status,
    b?.approval_status,
  );
  // The docked decision bar (routing + the approval gate) shows until the
  // borrower is decided; the decision outcome then takes its place.
  const decisionPending = effectiveApproval !== 'approved' && effectiveApproval !== 'rejected';
  const draftText = draftLoaded ? draft.body : '';
  const subjectReady = activeDraftChannel === 'sms' || draftSubject.trim().length > 0;
  const draftProofFresh = draftProofMatchesSnapshot(b, rec, draftProof?.sourceRefreshedAt);
  const draftReady = Boolean(
    draftLoaded
      && draftProofFresh
      && subjectReady
      && draftText.trim().length > 0,
  );
  const savedDraft = savedDraftKey ? savedDrafts[savedDraftKey] : undefined;
  const draftIsSaved = Boolean(
    savedDraft
      && savedDraft.generation_id === draftProof?.generationId
      && savedDraft.response_hash === draftProof?.responseHash
      && savedDraft.body === draftText
      && (savedDraft.subject ?? '') === (activeDraftChannel === 'sms' ? '' : draftSubject),
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
    if (!id || !draftReady || !draftProof) return;
    if (queryClient.isMutating({ mutationKey: offerMutationKeys.draftSave }) > 0) return;
    // mutate (not mutateAsync): a failure is read from draftSave.error.
    draftSave.mutate({
      borrower_id: id,
      generation_id: draftProof.generationId,
      response_hash: draftProof.responseHash,
    });
  };
  const resetCurrentDraft = () => {
    if (!id) return;
    draftSave.reset();
    removeSavedDraft(id, activeDraftChannel);
    setLoadGeneration((n) => n + 1);
    rereadSnapshotAndDraft();
  };

  const regenerateDraft = () => {
    if (!id || approving) return;
    setLoadGeneration((n) => n + 1);
    rereadSnapshotAndDraft();
  };

  const onApprove = async () => {
    // Synchronous latch: a decision for this borrower is already on the wire
    // (a double click, or the Lead Queue's write) — never a second POST.
    if (isDecisionPending(queryClient, id)) return;
    if (approving || snapshotReconciling || snapshot.reading || approverGate !== null) return;
    setApproveError(null);
    if (campaignBindingError) {
      setApproveError('Campaign handoff is incomplete. Reopen the saved campaign before approval.');
      return;
    }
    if (!draftReady) {
      setApproveError('Approval is disabled until the audited outreach draft loads from the backend.');
      return;
    }
    // Always the on-screen draft: never draftForApproval, never a second /draft.
    const body: OfferApproveBody = {
      offer_code: rec?.offer_code ?? b?.recommended_offer_code ?? null,
      evidence_ids: rec?.evidence_ids ?? b?.evidence_ids ?? [],
      draft_subject: activeDraftChannel === 'sms' ? null : draftSubject,
      draft_body: draftText,
      draft_generation_id: draftProof?.generationId ?? null,
      draft_response_hash: draftProof?.responseHash ?? null,
      draft_source_refreshed_at: draftProof?.sourceRefreshedAt ?? null,
      channel: activeDraftChannel,
      assigned_to_email: assignedTo || null,
      follow_up_in_days: followUpDays > 0 ? followUpDays : null,
      campaign_id: campaignBinding?.campaign_id ?? null,
      variant_name: campaignBinding?.variant_name ?? null,
      review_mode: 'individual',
    };
    const intent = intentFingerprint('approve', id, JSON.stringify(body));
    const variables = { decision: 'approve' as const, borrowerId: id, requestId: requestIds.idFor(intent), body };
    let res: ApproveResult;
    try {
      res = await approveMutation.mutateAsync(variables);
    } catch (err: unknown) {
      setApproveError(writeErrorCopy(err, "Couldn't write approval"));
      return;
    }
    if (!res.approved) {
      setApproveError('Approval endpoint returned approved=false.');
      return;
    }
    requestIds.settle(intent);
    setApproval(id, 'approved');
    // The audit-free decision history re-reads now (exact key: never the
    // dossier, whose read is an audited VIEW_BORROWER).
    void queryClient.invalidateQueries({ queryKey: queryKeys.borrowerDecisions(id), exact: true });
    setDecidedHere({
      id,
      generation: loadGeneration,
      auditId: res.audit_event_id ?? null,
      approvalId: res.approval_id ?? null,
    });
    const routedTo = res.assigned_to_email ?? (assignedTo || null);
    setApprovalRouting({ id, assignedTo: routedTo, followUpAt: res.follow_up_at ?? null });
    announceApprovalRouting(routedTo, res.follow_up_at ?? null, res.audit_event_id ?? null);
  };

  const onReject = async () => {
    if (isDecisionPending(queryClient, id)) return;
    if (approving || snapshotReconciling || approverGate !== null) return;
    if (campaignBindingError) {
      setApproveError('Campaign handoff is incomplete. Reopen the saved campaign before rejection.');
      return;
    }
    if (!rejectReviewOpen) {
      setRejectReviewOpen(true);
      return;
    }
    // Belt: the panel moves focus to Reason and sends nothing without one.
    if (rejectReasonCode === '') return;
    if (rejectReasonCode === 'other_with_text' && rejectRationale.trim().length === 0) {
      setApproveError('Rejection reason "Other" requires a rationale note.');
      return;
    }
    // Confirm reject waits for this open's snapshot read, like Approve.
    if (snapshot.reading) return;
    setApproveError(null);
    const body: OfferRejectBody = {
      offer_code: rec?.offer_code ?? b?.recommended_offer_code ?? null,
      evidence_ids: rec?.evidence_ids ?? b?.evidence_ids ?? [],
      channel: activeDraftChannel,
      rationale_code: rejectReasonCode,
      rationale: rejectRationale.trim() || null,
      campaign_id: campaignBinding?.campaign_id ?? null,
      variant_name: campaignBinding?.variant_name ?? null,
    };
    const intent = intentFingerprint('reject', id, JSON.stringify(body));
    const variables = { decision: 'reject' as const, borrowerId: id, requestId: requestIds.idFor(intent), body };
    let res: RejectResult;
    try {
      res = await rejectMutation.mutateAsync(variables);
    } catch (err: unknown) {
      setApproveError(writeErrorCopy(err, "Couldn't record rejection"));
      return;
    }
    if (!res.rejected) {
      setApproveError('Reject endpoint returned rejected=false.');
      return;
    }
    requestIds.settle(intent);
    setApproval(id, 'rejected');
    void queryClient.invalidateQueries({ queryKey: queryKeys.borrowerDecisions(id), exact: true });
    setDecidedHere({ id, generation: loadGeneration, auditId: res.audit_event_id ?? null, approvalId: null });
    setRejectReviewOpen(false);
    setRejectReasonCode('');
    setRejectRationale('');
  };

  if (snapshot.warmingUp) {
    return <OfferWarmingRoute borrowerId={id} warmingUp={snapshot.warmingUp} pager={pager} />;
  }

  if (snapshot.loadError) {
    return (
      <OfferLoadErrorRoute
        borrowerId={id}
        loadError={snapshot.loadError}
        notFound={snapshot.notFound}
        backTo={queueHref(queue)}
        pager={pager}
        onRetry={() => {
          setLoadGeneration((n) => n + 1);
          rereadSnapshotAndDraft();
        }}
      />
    );
  }

  if (snapshotReconciling && !b) {
    return <OfferSnapshotReconciliation borrowerId={id} />;
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
            {/* No Approve here: the hero shortcut (2026-04-22) let a reviewer
                approve before the loan-officer routing was on screen. The
                docked decision bar keeps routing and Approve together and in
                view at 1440x900 (2026-09-21 audit visual-v1). */}
            {/* Auto-offer Module 1 prototype: show the borrower-facing offer
                experience (the "click yes" vision). Clearly a mock, and a
                demo affordance: presenter mode only (D-shell-deviations-e1). */}
            {presenterMode && (
              <Button
                variant="ghost"
                size="sm"
                icon="user"
                onClick={() => setBorrowerPreviewOpen(true)}
                data-testid="preview-borrower-offer"
              >
                Preview borrower view
              </Button>
            )}
          </>
        )
      }
    >
      {pager}
      {snapshotReconciling && (
        <OfferSnapshotReconciliation borrowerId={id} inline />
      )}
      {campaignBindingError && (
        <div className="status-callout status-callout--danger mb-grid" role="alert">
          Campaign handoff is incomplete. Reopen the saved campaign and select a variant before taking action.
        </div>
      )}
      {campaignBinding && (
        <div
          className="status-callout status-callout--info mb-grid chip-row"
          role="status"
          data-testid="offer-campaign-provenance"
        >
          <Chip variant="success" icon="shield">Campaign-bound draft</Chip>
          <span className="mono" title={campaignBinding.campaign_id}>
            campaign {campaignBinding.campaign_id.slice(0, 12)}
          </span>
          <span>variant {campaignBinding.variant_name}</span>
          {draft.generatorLabel && <span>{draft.generatorLabel}</span>}
          {draft.generationMode && <span>{draft.generationMode.replace(/_/g, ' ')}</span>}
          {draftProof && (
            <span className="mono" title={draftProof.responseHash}>
              draft proof {draftProof.responseHash.slice(0, 12)}
            </span>
          )}
        </div>
      )}
      {BorrowerOfferPreviewMock && b && (
        <BorrowerOfferPreviewMock borrower={b} onClose={() => setBorrowerPreviewOpen(false)} />
      )}
      <OfferReviewGrid
        borrower={b}
        recommendation={rec}
        productLabel={productLabel}
        leadIsSaved={leadIsSaved}
        saveCurrentLead={saveCurrentLead}
        draftWarming={draft.warming}
        draftPending={draft.pending}
        draftLoaded={draftLoaded}
        draftError={draft.error}
        draftSubject={draftSubject}
        draftText={draftText}
        draftChannel={activeDraftChannel}
        allowedDraftChannels={allowedDraftChannels}
        draftProofFresh={draftProofFresh}
        onDraftChannelChange={(channel) => {
          if (allowedDraftChannels && !allowedDraftChannels.includes(channel)) return;
          draftSave.reset();
          setDraftChannel(channel);
          // The old effect re-read the whole snapshot on a channel switch;
          // the draft follows its new key (one POST for the new channel).
          snapshot.refetch();
        }}
        approving={approving}
        draftDisclosureVersion={draft.disclosureVersion}
        draftDisclosureState={draft.disclosureState}
        draftGeneratorLabel={draft.generatorLabel}
        draftGenerationMode={draft.generationMode}
        draftStrategy={draft.strategy}
        draftEvidence={draft.evidence}
        draftEvidenceAssets={draft.evidenceAssets}
        regenerateDraft={regenerateDraft}
        draftIsSaved={draftIsSaved}
        saveCurrentDraft={saveCurrentDraft}
        draftSavePending={draftSavePending}
        draftSaveError={draftSaveError}
        savedDraftExists={Boolean(savedDraft)}
        resetCurrentDraft={resetCurrentDraft}
        draftReady={draftReady}
        borrowerId={id}
        canAccessAdmin={canAccessAdmin}
      />

      <OfferDecisionOutcome
        borrowerId={b?.borrower_id ?? id}
        offerCode={rec?.offer_code ?? b?.recommended_offer_code ?? null}
        channel={activeDraftChannel}
        effectiveApproval={effectiveApproval}
        auditId={auditId}
        justDecided={justDecided}
        approvalId={approvalId}
        approveError={decisionPending ? null : approveError}
        score={b ? { opportunityScore: b.opportunity_score, confidence: b.confidence } : null}
        routing={approvalRouting?.id === id ? approvalRouting : null}
        queue={queue}
        nextId={queue ? queuePosition(queue, id)?.next ?? null : null}
      />
      <OfferPriorDecisions borrowerId={b?.borrower_id ?? id} />
      {decisionPending && (
        <OfferActionBar
          borrowerId={b?.borrower_id ?? null}
          salesTeam={salesTeam}
          assignedTo={assignedTo}
          onAssignedToChange={setAssignedTo}
          followUpDays={followUpDays}
          onFollowUpDaysChange={setFollowUpDays}
          approving={approving}
          onApprove={() => void onApprove()}
          onReject={() => void onReject()}
          approveDisabled={snapshotReconciling || snapshot.reading || !draftReady}
          isSubmitting={approving || snapshotReconciling}
          approverGate={approverGate}
          actorEmail={actorEmail}
          approveError={approveError}
          genieOpen={genieOpen}
          rejectReview={rejectReviewOpen && (
            <RejectRationalePanel
              reasonCode={rejectReasonCode}
              rationale={rejectRationale}
              onReasonChange={setRejectReasonCode}
              onRationaleChange={setRejectRationale}
              onCancel={() => {
                setRejectReviewOpen(false);
                setRejectRationale('');
                setRejectReasonCode('');
              }}
              onSubmit={() => void onReject()}
              submitDisabled={snapshot.reading}
            />
          )}
        />
      )}
    </PageShell>
  );
}

// The Lead Queue's expanded row renders the ApprovalBanner this route ships
// (audit tables-01): LeadRowPreview takes it from this module through
// OfferOrchestratorRoute.preload(), which a row expand already runs, so the
// banner is bundled once, here.
export { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
