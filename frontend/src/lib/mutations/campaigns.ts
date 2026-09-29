/**
 * Portfolio Builder's governed campaign writes on the TanStack mutation layer
 * (2026-09-21 audit stack-09 item 2, runtime-06 (e), states-07 item 3).
 *
 * Both writes are pessimistic: no onMutate, no setQueryData. The page shows a
 * saved build or an archived campaign only after the server returned, and the
 * saved-campaign list is re-read from the server rather than patched.
 *
 *   - create: POST /api/portfolio/create. The Idempotency-Key is the intent's
 *     request_id (the caller keeps one per save-panel session + payload, so a
 *     "try again" after a failure replays the same key). onSuccess RETURNS the
 *     list refresh, so mutateAsync resolves only once the list is re-read,
 *     exactly like the awaited refetch it replaced.
 *   - archive: PATCH /api/campaigns/{id} to 'archived' with the reviewed
 *     rationale (the API writes the audit event). Its success refreshes the
 *     list in the background, as before.
 *
 * networkMode 'always' and retry false on both: a write started offline fails
 * at once instead of firing later, and nothing re-sends a write on its own.
 */
import { useMutation, type QueryClient } from '@tanstack/react-query';
import type { CampaignSummary, PortfolioCreateResponse } from '../../types';
import { api } from '../api';
import { queryKeys } from '../queryKeys';

export const campaignMutationKeys = {
  create: ['mip', 'campaigns', 'create'] as const,
  archive: ['mip', 'campaigns', 'archive'] as const,
};

/** The campaign configuration portfolio-builder.logic's buildCampaignConfig emits. */
export interface CampaignCreateConfig {
  suppression_policy: Record<string, unknown>;
  message_variants: Record<string, unknown>[];
  channel_cascade: Record<string, unknown>[];
  send_window: Record<string, unknown>;
  holdout: Record<string, unknown>;
  roi_assumptions: Record<string, unknown>;
  household_dedup: Record<string, unknown>;
}

export interface CreateCampaignVariables {
  name: string;
  criteria: Record<string, unknown>;
  config: CampaignCreateConfig;
  requestId: string;
}

export interface ArchiveCampaignVariables {
  campaignId: string;
  /** For the feedback copy only; never sent. */
  name: string;
}

/** The reviewed archive rationale, verbatim from before the port. */
export const CAMPAIGN_ARCHIVE_RATIONALE =
  'Archived because immutable campaign treatment proof was unavailable.';

/** Re-read the saved-campaign list (the list only: a new campaign changes no other entry). */
function refreshCampaignList(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.campaigns(), exact: true });
}

export function useCreateCampaign(queryClient: QueryClient) {
  return useMutation<PortfolioCreateResponse, Error, CreateCampaignVariables>(
    {
      mutationKey: campaignMutationKeys.create,
      mutationFn: ({ name, criteria, config, requestId }) =>
        api.portfolioCreate(name, criteria, { ...config, request_id: requestId }),
      networkMode: 'always',
      retry: false,
      onSuccess: () => refreshCampaignList(queryClient),
    },
    queryClient,
  );
}

export function useArchiveCampaign(queryClient: QueryClient) {
  return useMutation<CampaignSummary, Error, ArchiveCampaignVariables>(
    {
      mutationKey: campaignMutationKeys.archive,
      mutationFn: ({ campaignId }) => api.campaignStatus(campaignId, 'archived', CAMPAIGN_ARCHIVE_RATIONALE),
      networkMode: 'always',
      retry: false,
      onSuccess: () => {
        void refreshCampaignList(queryClient);
      },
    },
    queryClient,
  );
}
