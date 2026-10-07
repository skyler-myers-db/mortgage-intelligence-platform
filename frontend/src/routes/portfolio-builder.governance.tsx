import { useState } from 'react';
import { useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { Link } from 'react-router';
import { useApp } from '../components/AppContext';
import { Icon } from '../components/Icon';
import { Button, SurfaceTitle } from '../components/Primitives';
import { AsyncStatus, type AsyncQuery } from '../components/ui/AsyncState';
import { Field } from '../components/ui/Field';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { campaignMutationKeys, useArchiveCampaign } from '../lib/mutations/campaigns';
import { formatCount } from '../lib/formatters';
import { formatDate, parseBackendTimestamp } from '../lib/time';
import type { CampaignListResponse, CampaignSummary, PortfolioPreview } from '../types';
import {
  campaignCriteriaSummary,
  groupSavedCampaigns,
} from './portfolio-builder.logic';
import {
  savedCampaignCanArchive,
  savedCampaignLeadQueueUrl,
  savedCampaignVariants,
} from './portfolio-builder.saved-campaigns';

type CampaignArchiveFeedback = {
  campaignId: string;
  state: 'pending' | 'success' | 'error';
  message: string;
};

/** The archive's feedback, derived from the mutation's state (same copy as before the port). */
function archiveFeedbackFor(
  campaign: { campaignId: string; name: string } | undefined,
  status: 'idle' | 'pending' | 'success' | 'error',
): CampaignArchiveFeedback | null {
  if (!campaign || status === 'idle') return null;
  const { campaignId, name } = campaign;
  if (status === 'pending') return { campaignId, state: 'pending', message: `Archiving ${name}…` };
  if (status === 'success') {
    return { campaignId, state: 'success', message: `${name} was archived with an audited campaign transition.` };
  }
  return {
    campaignId,
    state: 'error',
    message: `${name} could not be archived. Its quarantine remains in place; try again.`,
  };
}

function formatSavedCampaignDate(iso: string | null): string | null {
  const parsed = parseBackendTimestamp(iso);
  return parsed ? formatDate(parsed, { withYear: false }) : null;
}

export function CampaignBuildGuard({ preview }: { preview: PortfolioPreview }) {
  const eligible = preview.campaign_build_eligible === true;
  const contactCount = preview.campaign_build_contact_count;
  const limit = preview.campaign_build_limit ?? 10_000;
  const overLimit = contactCount !== null && contactCount > limit;

  return (
    <div
      className={`approval campaign-build-guard ${eligible ? '' : 'campaign-build-guard--blocked'}`}
      role={eligible ? 'status' : 'alert'}
      aria-live="polite"
      data-testid="campaign-build-guard"
    >
      <div className="approval__ico">
        <Icon name="shield" size={16} />
      </div>
      <div className="approval__body">
        <div className="approval__title">
          {eligible
            ? 'Governed saved-contact batch verified'
            : 'Refine this build before saving a campaign'}
        </div>
        <div className="approval__sub">
          {eligible ? (
            <>
              Server treatment preflight: {typeof contactCount === 'number' ? formatCount(contactCount) : 'verified'} of{' '}
              {formatCount(limit)} maximum saved treatment contacts after suppression,
              frequency-cap, and household-primary selection.
            </>
          ) : overLimit ? (
            <>
              This treatment preflight contains {formatCount(contactCount)} contacts,
              above the governed {formatCount(limit)}-contact treatment limit. Narrow
              geography, relationship, product, equity, or other filters, then run the build again.
            </>
          ) : (
            <>
              The server did not verify campaign-build eligibility for the governed{' '}
              {formatCount(limit)}-contact limit. Run the build again before saving.
            </>
          )}
        </div>
      </div>
      <span className={`chip ${eligible ? 'chip--success' : 'chip--warning'}`}>
        {eligible ? 'Save eligible' : 'Save blocked'}
      </span>
    </div>
  );
}

/** The saved-campaign read in AsyncStatus's shape (states-04): no warming loop, one Retry = one refetch. */
function savedCampaignsStatus(query: UseQueryResult<CampaignListResponse>): AsyncQuery<CampaignListResponse> {
  return {
    data: query.data ?? null,
    warmingUp: null,
    error: query.error,
    manualRetry: () => void query.refetch(),
    isFetching: query.isFetching,
    isPlaceholderData: query.isPlaceholderData,
    errorUpdatedAt: query.errorUpdatedAt || null,
  };
}

export function SavedCampaignsPanel({ query }: { query: UseQueryResult<CampaignListResponse> }) {
  const campaigns: CampaignSummary[] = query.data?.campaigns ?? [];
  const { setDrawer } = useApp();
  const queryClient = useQueryClient();
  const [selectedVariants, setSelectedVariants] = useState<Record<string, string>>({});
  // Pessimistic (states-07): pending, success and failure are the archive
  // mutation's own state; the list is re-read from the server on success.
  const archive = useArchiveCampaign(queryClient);
  const archiveFeedback = archiveFeedbackFor(archive.variables, archive.status);

  const onArchive = (campaign: CampaignSummary) => {
    // Synchronous latch: one archive on the wire at a time.
    if (!savedCampaignCanArchive(campaign)) return;
    if (queryClient.isMutating({ mutationKey: campaignMutationKeys.archive }) > 0) return;
    // mutate, not mutateAsync: a failure is read from the mutation's state.
    archive.mutate({ campaignId: campaign.campaign_id, name: campaign.name });
  };

  return (
    <div className="surface mt-4">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="doc" size={14} />
          </div>
          <div>
            <SurfaceTitle>Saved campaigns</SurfaceTitle>
            <div className="muted fs-12">Drafts and review status for portfolio builds.</div>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          icon="tweak"
          onClick={() => void query.refetch()}
          aria-label="Refresh saved campaigns"
        >
          Refresh
        </Button>
      </div>
      <div className="surface__body">
        {archiveFeedback && (
          <div
            className={`status-callout ${archiveFeedback.state === 'error' ? 'status-callout--danger' : 'status-callout--info'}`}
            role={archiveFeedback.state === 'error' ? 'alert' : 'status'}
            aria-live="polite"
            data-testid="campaign-archive-feedback"
          >
            {archiveFeedback.message}
          </div>
        )}
        {query.isPending ? (
          <div className="muted fs-12">Loading campaigns…</div>
        ) : query.isError ? (
          <AsyncStatus query={savedCampaignsStatus(query)} subject="Saved campaigns" compact />
        ) : campaigns.length === 0 ? (
          <div className="muted fs-12">No saved campaigns.</div>
        ) : (
          <div className="saved-workspace">
            <div className="saved-workspace__summary">
              <span>{formatCount(campaigns.length)} saved</span>
              <span>eligible-only policy required before approval</span>
            </div>
            {groupSavedCampaigns(campaigns).slice(0, 8).map((row) => {
              const campaign = row.campaign;
              const variants = savedCampaignVariants(campaign);
              const canArchive = savedCampaignCanArchive(campaign);
              const archivePending = canArchive
                && archiveFeedback?.campaignId === campaign.campaign_id
                && archiveFeedback.state === 'pending';
              const selectedName = selectedVariants[campaign.campaign_id]
                ?? variants[0]?.variantName
                ?? '';
              const selected = variants.find((variant) => variant.variantName === selectedName);
              const householdEnabled = campaign.household_dedup?.enabled === true;
              const suppressedCount = campaign.household_summary?.suppressed_co_owner_count ?? 0;
              const grouped = row.draftCount > 1;
              const latestSaved = grouped ? formatSavedCampaignDate(row.latestAt) : null;
              return (
                <div key={campaign.campaign_id} className="saved-workspace__item">
                  <span
                    className={`status-dot status-dot--${campaign.actionable === false ? 'warn' : 'ok'}`}
                    aria-hidden="true"
                  />
                  <div className="saved-workspace__body">
                    <span className="text-1">{campaign.name}</span>
                    <span>{campaign.status.replace(/_/g, ' ')} · {campaignCriteriaSummary(campaign)}</span>
                    {campaign.actionable === false && (
                      <span className="chip chip--warning chip--compact">
                        {campaign.actionability_issue === 'treatment_unbound'
                          ? 'Immutable treatment proof unavailable · Lead Queue locked'
                          : 'Rebuild this campaign before opening its cohort'}
                      </span>
                    )}
                    {campaign.actionable !== false && (grouped || householdEnabled || selected) && (
                      <div className="saved-workspace__proof">
                        {grouped && <span className="chip chip--neutral chip--compact">×{row.draftCount} drafts</span>}
                        {grouped && latestSaved && <span className="muted fs-11">latest {latestSaved}</span>}
                        {householdEnabled && (
                          <>
                            <span className="chip chip--warning">
                              {formatCount(suppressedCount)} co-owner
                              {suppressedCount === 1 ? '' : 's'} suppressed
                            </span>
                            <button
                              type="button"
                              className="evidence-chip"
                              onClick={() => setDrawer(DRAWER_SOURCES.householdRollup)}
                            >
                              <Icon name="link" size={9} className="e-ico" />
                              <span className="evidence-chip__label">household_rollup</span>
                            </button>
                          </>
                        )}
                        {selected && (
                          <>
                            <span className={`chip ${selected.generationMode === 'supervisor' ? 'chip--success' : 'chip--neutral'} chip--compact`}>
                              {selected.generatorLabel}
                            </span>
                            <span className="mono muted fs-11" title={campaign.campaign_id}>
                              campaign {campaign.campaign_id.slice(0, 12)}
                            </span>
                            <span className={`chip ${selected.verifiedAtCreation ? 'chip--success' : 'chip--warning'} chip--compact`}>
                              {selected.verifiedAtCreation ? 'Verified at creation' : 'Saved copy only'}
                            </span>
                            <Field className="field--inline" label={`Message variant for ${campaign.name}`} labelHidden>
                              {(control) => (
                                <select
                                  {...control}
                                  className="outreach-routing__select"
                                  value={selectedName}
                                  onChange={(event) => setSelectedVariants((current) => ({
                                    ...current,
                                    [campaign.campaign_id]: event.target.value,
                                  }))}
                                >
                                  {variants.map((variant) => (
                                    <option key={variant.variantName} value={variant.variantName}>
                                      Variant {variant.variantName}
                                    </option>
                                  ))}
                                </select>
                              )}
                            </Field>
                            <Link
                              to={savedCampaignLeadQueueUrl(campaign, selected.variantName)}
                              className="btn btn--primary btn--sm"
                              aria-label={`Open ${campaign.name} variant ${selected.variantName} in Lead Queue`}
                            >
                              Open lead queue
                              <Icon name="chevright" size={12} />
                            </Link>
                          </>
                        )}
                      </div>
                    )}
                    {canArchive && (
                      <div className="saved-workspace__proof">
                        <Button
                          variant="ghost"
                          size="sm"
                          icon="audit"
                          onClick={() => onArchive(campaign)}
                          disabled={archivePending}
                          aria-busy={archivePending}
                          aria-label={`Archive quarantined campaign ${campaign.name}`}
                          data-testid={`archive-campaign-${campaign.campaign_id}`}
                        >
                          {archivePending ? 'Archiving…' : 'Archive campaign'}
                        </Button>
                        <span className="muted fs-11">
                          Archive is the only permitted transition; the API writes the audit event.
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
