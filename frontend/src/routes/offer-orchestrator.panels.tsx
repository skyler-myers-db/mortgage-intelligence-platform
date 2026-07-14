import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import { BorrowerTruthFlags } from '../components/mortgage/BorrowerTruthFlags';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { Reveal } from '../components/fx/Reveal';
import { descriptorFor, drawerForAsset } from '../lib/drawerSources';
import { offerDisplayLabel, offerRationale, offerShortDescription } from '../lib/offerLanguage';
import {
  OUTREACH_CHANNELS,
  REJECT_REASONS,
  type OutreachChannel,
  type RejectReasonCode,
} from './offer-orchestrator.constants';
import { humanizeThresholdKey, shortSourceLabel } from './offer-orchestrator.helpers';

export function OfferOrchestratorEmptyState() {
  return (
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
          The app selects one primary offer path, shows the alternatives it
          ruled out, and keeps outreach in human review.
        </p>
      </div>
    </div>
  );
}

interface OfferOrchestratorEmptyHeroProps {
  to: string;
}

export function OfferOrchestratorEmptyHero({ to }: OfferOrchestratorEmptyHeroProps) {
  return (
    <Link className="btn btn--primary" to={to}>
      Browse lead queue
      <Icon name="chevright" size={14} />
    </Link>
  );
}

interface RejectRationalePanelProps {
  reasonCode: RejectReasonCode;
  rationale: string;
  onReasonChange: (reason: RejectReasonCode) => void;
  onRationaleChange: (rationale: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export function RejectRationalePanel({
  reasonCode,
  rationale,
  onReasonChange,
  onRationaleChange,
  onCancel,
  onSubmit,
}: RejectRationalePanelProps) {
  return (
    <form
      className="surface mb-grid"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="decision-panel">
        <div>
          <div className="h-4">Reject rationale</div>
          <div className="muted fs-12">
            Capture a committee-visible reason before dropping this lead.
          </div>
        </div>
        <label className="decision-panel__field">
          <span className="field__label">Reason</span>
          <select
            value={reasonCode}
            onChange={(e) => onReasonChange(e.target.value as RejectReasonCode)}
          >
            {REJECT_REASONS.map((reason) => (
              <option key={reason.code} value={reason.code}>{reason.label}</option>
            ))}
          </select>
        </label>
        <label className="decision-panel__field decision-panel__field--wide">
          <span className="field__label">Rationale note</span>
          <textarea
            value={rationale}
            onChange={(e) => onRationaleChange(e.target.value)}
            maxLength={500}
            placeholder="Optional unless reason is Other."
          />
        </label>
        <div className="decision-panel__actions">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onCancel}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            icon="cross"
            disabled={reasonCode === 'other_with_text' && rationale.trim().length === 0}
          >
            Confirm reject
          </Button>
        </div>
      </div>
    </form>
  );
}

interface OfferReviewGridProps {
  borrower: Borrower360Type | null;
  recommendation: OfferRecommendation | null;
  productLabel: string;
  leadIsSaved: boolean;
  saveCurrentLead: () => void;
  draftWarming: WarmingUpState | null;
  draftPending?: boolean;
  draftLoaded: boolean;
  draftError: string | null;
  draftSubject: string;
  onDraftSubjectChange: (subject: string) => void;
  draftText: string;
  onDraftChange: (body: string) => void;
  draftChannel: OutreachChannel;
  draftDirty?: boolean;
  draftProofFresh: boolean;
  onDraftChannelChange: (channel: OutreachChannel) => void;
  approving: boolean;
  draftDisclosureVersion: string | null;
  draftDisclosureState: string | null;
  draftGeneratorLabel: string | null;
  draftGenerationMode: 'supervisor' | 'governed_fallback' | null;
  draftStrategy: string | null;
  draftEvidence: string[];
  draftEvidenceAssets: string[];
  regenerateDraft: () => void;
  draftIsSaved: boolean;
  saveCurrentDraft: () => void;
  draftSavePending?: boolean;
  draftSaveError?: string | null;
  savedDraftExists: boolean;
  resetCurrentDraft: () => void;
  draftReady: boolean;
  borrowerId: string | null;
  canAccessAdmin?: boolean;
}

export function OfferReviewGrid({
  borrower,
  recommendation,
  productLabel,
  leadIsSaved,
  saveCurrentLead,
  draftWarming,
  draftPending = false,
  draftLoaded,
  draftError,
  draftSubject,
  onDraftSubjectChange,
  draftText,
  onDraftChange,
  draftChannel,
  draftDirty = false,
  draftProofFresh,
  onDraftChannelChange,
  approving,
  draftDisclosureVersion,
  draftDisclosureState,
  draftGeneratorLabel,
  draftGenerationMode,
  draftStrategy,
  draftEvidence,
  draftEvidenceAssets,
  regenerateDraft,
  draftIsSaved,
  saveCurrentDraft,
  draftSavePending = false,
  draftSaveError = null,
  savedDraftExists,
  resetCurrentDraft,
  draftReady,
  borrowerId,
  canAccessAdmin = false,
}: OfferReviewGridProps) {
  return (
    <div className="layoutA-grid">
      <PrimaryOfferPanel
        borrower={borrower}
        recommendation={recommendation}
        productLabel={productLabel}
        leadIsSaved={leadIsSaved}
        saveCurrentLead={saveCurrentLead}
      />
      <DraftOutreachPanel
        borrower={borrower}
        borrowerId={borrowerId}
        draftWarming={draftWarming}
        draftPending={draftPending}
        draftLoaded={draftLoaded}
        draftError={draftError}
        draftSubject={draftSubject}
        onDraftSubjectChange={onDraftSubjectChange}
        draftText={draftText}
        onDraftChange={onDraftChange}
        draftChannel={draftChannel}
        draftDirty={draftDirty}
        draftProofFresh={draftProofFresh}
        onDraftChannelChange={onDraftChannelChange}
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
        draftSavePending={draftSavePending}
        draftSaveError={draftSaveError}
        savedDraftExists={savedDraftExists}
        resetCurrentDraft={resetCurrentDraft}
        draftReady={draftReady}
        canAccessAdmin={canAccessAdmin}
      />
    </div>
  );
}

interface PrimaryOfferPanelProps {
  borrower: Borrower360Type | null;
  recommendation: OfferRecommendation | null;
  productLabel: string;
  leadIsSaved: boolean;
  saveCurrentLead: () => void;
}

function PrimaryOfferPanel({
  borrower,
  recommendation,
  productLabel,
  leadIsSaved,
  saveCurrentLead,
}: PrimaryOfferPanelProps) {
  return (
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
          {borrower && <ScoreBadge value={borrower.opportunity_score} />}
        </div>
        {recommendation && (
          <p className="muted fs-12 mt-1 flush">
            {offerShortDescription(recommendation.offer_code)}
          </p>
        )}
        {recommendation ? (
          <p className="body mt-2">
            {recommendation.rationale ?? offerRationale(recommendation.offer_code, borrower?.why_now)}
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
          {recommendation
            ? (recommendation.sources ?? []).map((source, idx) => {
                // Prefer the backend-supplied human-readable label
                // (added 2026-04-22). Fall back to the trailing UC
                // segment so anything that hasn't been mapped still
                // reads sensibly.
                const label = recommendation.source_labels?.[idx]?.display_label
                  ?? shortSourceLabel(source);
                return (
                  <EvidenceChip key={source} source={descriptorFor(source)}>
                    {label}
                  </EvidenceChip>
                );
              })
            : Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} width={96} height={18} rounded="sm" />
              ))}
        </div>
        {borrower && (
          <div className="mt-3">
            <div className="eyebrow mb-2">Borrower flags</div>
            <BorrowerTruthFlags borrower={borrower} />
          </div>
        )}
        {borrower && (
          <div className="chip-row mt-3">
            <Button
              variant={leadIsSaved ? 'ghost' : 'default'}
              size="sm"
              icon={leadIsSaved ? 'check' : 'tag'}
              onClick={saveCurrentLead}
              aria-label={`${leadIsSaved ? 'Saved' : 'Save'} borrower ${borrower.borrower_id}`}
            >
              {leadIsSaved ? 'Lead saved' : 'Save lead'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

interface DraftOutreachPanelProps {
  borrower: Borrower360Type | null;
  borrowerId: string | null;
  draftWarming: WarmingUpState | null;
  draftPending?: boolean;
  draftLoaded: boolean;
  draftError: string | null;
  draftSubject: string;
  onDraftSubjectChange: (subject: string) => void;
  draftText: string;
  onDraftChange: (body: string) => void;
  draftChannel: OutreachChannel;
  draftDirty?: boolean;
  draftProofFresh: boolean;
  onDraftChannelChange: (channel: OutreachChannel) => void;
  approving: boolean;
  draftDisclosureVersion: string | null;
  draftDisclosureState: string | null;
  draftGeneratorLabel: string | null;
  draftGenerationMode: 'supervisor' | 'governed_fallback' | null;
  draftStrategy: string | null;
  draftEvidence: string[];
  draftEvidenceAssets: string[];
  regenerateDraft: () => void;
  draftIsSaved: boolean;
  saveCurrentDraft: () => void;
  draftSavePending?: boolean;
  draftSaveError?: string | null;
  savedDraftExists: boolean;
  resetCurrentDraft: () => void;
  draftReady: boolean;
  canAccessAdmin?: boolean;
}

function DraftOutreachPanel({
  borrower,
  borrowerId,
  draftWarming,
  draftPending = false,
  draftLoaded,
  draftError,
  draftSubject,
  onDraftSubjectChange,
  draftText,
  onDraftChange,
  draftChannel,
  draftDirty = false,
  draftProofFresh,
  onDraftChannelChange,
  approving,
  draftDisclosureVersion,
  draftDisclosureState,
  draftGeneratorLabel,
  draftGenerationMode,
  draftStrategy,
  draftEvidence,
  draftEvidenceAssets,
  regenerateDraft,
  draftIsSaved,
  saveCurrentDraft,
  draftSavePending = false,
  draftSaveError = null,
  savedDraftExists,
  resetCurrentDraft,
  draftReady,
  canAccessAdmin = false,
}: DraftOutreachPanelProps) {
  const [regenerateReviewOpen, setRegenerateReviewOpen] = useState(false);
  const [pendingChannel, setPendingChannel] = useState<OutreachChannel | null>(null);
  return (
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
        {draftPending && !draftLoaded && !draftWarming && borrower && (
          <div className="muted fs-12 mb-3" role="status" data-testid="draft-loading-note">
            Loading audited outreach draft…
          </div>
        )}
        {!draftLoaded && !draftWarming && !draftPending && borrower && (
          <div
            data-testid="draft-unavailable-note"
            className="status-callout status-callout--warning mb-3"
            role="alert"
          >
            <span>{draftError ?? 'Offer draft unavailable. Approval is disabled until the audited draft loads.'}</span>
            {draftError && (
              <div className="chip-row mt-2">
                <Button
                  variant="default"
                  size="sm"
                  icon="play"
                  onClick={regenerateDraft}
                  disabled={approving || !borrowerId}
                >
                  Retry draft
                </Button>
              </div>
            )}
          </div>
        )}
        {draftLoaded && !draftProofFresh && (
          <div
            data-testid="draft-proof-stale-note"
            className="status-callout status-callout--warning mb-3"
            role="alert"
          >
            <span>The borrower data changed after this draft was generated. Regenerate it before approval.</span>
            <div className="chip-row mt-2">
              <Button
                variant="default"
                size="sm"
                icon="play"
                onClick={regenerateDraft}
                disabled={approving || draftSavePending || !borrowerId}
              >
                Regenerate draft
              </Button>
            </div>
          </div>
        )}
        {draftChannel !== 'sms' && (
          <label className="field mb-3">
            <span className="field__label">Subject</span>
            <input
              aria-label="Outreach subject — review only"
              value={draftSubject}
              maxLength={120}
              onChange={(e) => {
                if (!draftLoaded) return;
                onDraftSubjectChange(e.target.value);
              }}
              disabled={!draftLoaded || draftSavePending}
              data-testid="outreach-subject"
            />
          </label>
        )}
        <textarea
          key={borrower?.borrower_id ?? 'empty'}
          aria-label="Outreach draft — review only"
          value={draftText}
          onChange={(e) => {
            if (!draftLoaded) return;
            onDraftChange(e.target.value);
          }}
          disabled={!draftLoaded || draftSavePending}
          data-testid="outreach-draft"
          className="route-textarea route-textarea--outreach"
        />
        <p className="muted fs-12 mt-2">
          A licensed loan officer must review the offer, disclosures, and borrower-facing copy before approval.
        </p>
        {draftGeneratorLabel && (
          <div className="offer-message-intelligence mt-3" data-testid="offer-message-intelligence">
            <div className="split-row">
              <Chip
                variant={!draftDirty && draftGenerationMode === 'supervisor' ? 'success' : 'neutral'}
                icon={!draftDirty && draftGenerationMode === 'supervisor' ? 'sparkle' : 'doc'}
              >
                {draftGeneratorLabel}
              </Chip>
              <Button
                variant="ghost"
                size="sm"
                icon="sparkle"
                onClick={() => setRegenerateReviewOpen(true)}
                disabled={approving || draftSavePending || !borrowerId}
              >
                Regenerate
              </Button>
            </div>
            {regenerateReviewOpen && (
              <div className="status-callout status-callout--warning" role="alert">
                <span>Regenerating replaces the subject and message currently in the editor.</span>
                <div className="chip-row mt-2">
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => {
                      setRegenerateReviewOpen(false);
                      regenerateDraft();
                    }}
                  >
                    Replace current draft
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setRegenerateReviewOpen(false)}
                  >
                    Keep current draft
                  </Button>
                </div>
              </div>
            )}
            {draftStrategy && <p className="body flush">{draftStrategy}</p>}
            {draftEvidence.length > 0 && (
              <div className="chip-row" aria-label="Message evidence">
                {draftEvidence.map((item) => (
                  <Chip key={item} variant="neutral">{item}</Chip>
                ))}
              </div>
            )}
            {draftEvidenceAssets.length > 0 && (
              <div className="chip-row" aria-label="Message source assets">
                {draftEvidenceAssets.map((asset) => {
                  const source = drawerForAsset(asset);
                  return source ? (
                    <EvidenceChip key={asset} source={source}>{asset}</EvidenceChip>
                  ) : null;
                })}
              </div>
            )}
          </div>
        )}
        <div className="chip-row mt-3">
          {OUTREACH_CHANNELS.map((channel) => (
            <Button
              key={channel}
              variant={draftChannel === channel ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => {
                if (channel === draftChannel) return;
                if (draftDirty) {
                  setPendingChannel(channel);
                  return;
                }
                onDraftChannelChange(channel);
              }}
              disabled={approving || draftSavePending}
            >
              {channel === 'direct_mail' ? 'Direct mail' : channel.toUpperCase()}
            </Button>
          ))}
          {canAccessAdmin ? (
            <Link
              className="chip chip--neutral offer-cadence-link"
              to="/admin-config#offer-rules"
              title="Open governed offer rules for follow-up cadence context"
            >
              <Icon name="link" size={10} />
              <span className="chip__label">LO call follow-up within 5 days</span>
            </Link>
          ) : (
            <Chip variant="neutral">LO call follow-up within 5 days</Chip>
          )}
          {draftDisclosureVersion && (
            <Chip variant="success" icon="shield">
              Disclosure {draftDisclosureVersion} · {draftDisclosureState ?? 'state fallback'}
            </Chip>
          )}
          <Button
            variant={draftIsSaved ? 'ghost' : 'default'}
            size="sm"
            icon={draftIsSaved ? 'check' : 'doc'}
            onClick={() => void saveCurrentDraft()}
            disabled={!borrowerId || !draftReady || draftSavePending}
            aria-label={`Save outreach draft for ${borrowerId ?? 'borrower'}`}
          >
            {draftSavePending ? 'Saving…' : draftIsSaved ? 'Draft saved' : 'Save draft'}
          </Button>
          {savedDraftExists && (
            <Button
              variant="ghost"
              size="sm"
              icon="cross"
              onClick={resetCurrentDraft}
              disabled={draftSavePending}
              aria-label={`Reset saved outreach draft for ${borrowerId ?? 'borrower'}`}
            >
              Reset draft
            </Button>
          )}
        </div>
        {draftSaveError && (
          <div className="status-callout status-callout--danger mt-3" role="alert">
            {draftSaveError}
          </div>
        )}
        {pendingChannel && (
          <div className="status-callout status-callout--warning mt-3" role="alert">
            <span>
              Switching to {pendingChannel === 'direct_mail' ? 'direct mail' : pendingChannel.toUpperCase()}
              {' '}replaces the unsaved subject and message currently in the editor.
            </span>
            <div className="chip-row mt-2">
              <Button
                variant="default"
                size="sm"
                onClick={() => {
                  const channel = pendingChannel;
                  setPendingChannel(null);
                  onDraftChannelChange(channel);
                }}
                disabled={draftSavePending}
              >
                Switch channel
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPendingChannel(null)}
              >
                Keep current edits
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

interface OfferDetailsRowsProps {
  recommendation: OfferRecommendation | null;
}

export function OfferDetailsRows({ recommendation }: OfferDetailsRowsProps) {
  return (
    <Reveal className="layoutA-grid mt-grid">
      <AlternativesPanel recommendation={recommendation} />
      <ThresholdsPanel recommendation={recommendation} />
    </Reveal>
  );
}

function AlternativesPanel({ recommendation }: OfferDetailsRowsProps) {
  return (
    <div className="surface">
      <div className="surface__hdr">
        <Icon name="doc" size={14} className="icon-accent" />
        <div>
          <div className="eyebrow">Considered alternatives</div>
          <div className="h-4 mt-1">
            {recommendation ? `${recommendation.alternatives.length} other product${recommendation.alternatives.length === 1 ? '' : 's'} ruled out` : 'Loading…'}
          </div>
        </div>
      </div>
      <div className="surface__body">
        {recommendation && recommendation.alternatives.length === 0 && (
          <p className="muted body flush">
            No alternatives considered — no trigger fires.
          </p>
        )}
        {recommendation && recommendation.alternatives.length > 0 && (
          <div className="stack-md">
            {recommendation.alternatives.map((alt) => (
              <div
                key={alt.offer_code}
                className="alt-card"
              >
                <div className="split-row">
                  <div className="fw-600 text-1">
                    {offerDisplayLabel(alt.offer_code, alt.product_label)}
                  </div>
                  <Chip variant="neutral" className="mono">{alt.offer_code}</Chip>
                </div>
                <p className="body muted flush">{alt.reason_not_chosen}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ThresholdsPanel({ recommendation }: OfferDetailsRowsProps) {
  return (
    <div className="surface">
      <div className="surface__hdr">
        <Icon name="shield" size={14} className="icon-accent" />
        <div>
          <div className="eyebrow">Thresholds applied</div>
          <div className="h-4 mt-1">Admin config at decision time</div>
        </div>
      </div>
      <div className="surface__body">
        {recommendation ? (
          <div className="threshold-grid">
            {Object.entries(recommendation.thresholds_applied).map(([k, v]) => (
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
  );
}
