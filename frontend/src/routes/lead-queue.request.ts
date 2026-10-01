/**
 * The Lead Queue's filter state derived from its URL, as pure functions: the
 * same derivation lead-queue.tsx performs inline, so the audit-free facet
 * counts and the saved-views panel read exactly the request and the share
 * grammar the queue itself uses. Parity with the route is pinned in
 * lead-queue.filterBar.test.tsx (the leads query key and the Copy-link URL
 * for a battery of URLs). W5c moves lead-queue.tsx onto these.
 */
import type { LeadsRequest } from '../lib/leadsQuery';
import { CITY_STATE_PAIR_RE } from '../lib/cityStateFilter';
import {
  isAssignedToMe,
  parseBorrowerIds,
  parseCsvParam,
  parseFunnelStage,
  parsePortfolioCriteria,
  parseSegmentCodes,
  parseTargetLenderRef,
  type LeadQueueExportFiltersInput,
} from './lead-queue.filters';

type ApprovalFilter = 'pending' | 'approved' | 'rejected' | 'hold' | 'any';
type OutreachFilter = 'none' | 'queued' | 'actioned' | 'sent' | 'bounced' | 'replied' | 'any';

/** The TARGET LIEN HOLDER options: the configured refs, or `['All']` while none are known. */
export function leadQueueLenderRefs(configured: readonly string[] | null | undefined): string[] {
  const values = configured?.filter(Boolean);
  return values && values.length > 0 ? [...values] : ['All'];
}

/**
 * The allowlisted filter input the export and Copy link read, with
 * `assignedTo` as the RAW URL value (`me` stays `me`).
 */
export function leadQueueFilterInputFromSearchParams(
  sp: URLSearchParams,
  allowedLenderRefs: readonly string[],
): LeadQueueExportFiltersInput {
  const approvalStatus = (sp.get('approval_status') ?? 'any').toLowerCase();
  const outreachStatus = (sp.get('outreach_status') ?? 'any').toLowerCase();
  return {
    segment: parseSegmentCodes(sp.get('segment'))[0],
    segmentCodes: parseSegmentCodes(sp.get('segment_codes')),
    segmentMode: sp.get('segment_mode')?.trim().toLowerCase() === 'all' ? 'all' : 'any',
    stateFilter: (sp.get('state') ?? '').toUpperCase() || undefined,
    zipFilter: (sp.get('zip') ?? '').trim() || undefined,
    stateFilters: parseCsvParam(sp.get('states'), /^[A-Z]{2}$/, 20),
    zipFilters: parseCsvParam(sp.get('zips'), /^\d{5}$/, 50),
    cityFilters: parseCsvParam(sp.get('cities'), CITY_STATE_PAIR_RE, 50),
    borrowerIdFilters: parseBorrowerIds(sp.get('borrower_ids')),
    countyFilter: (sp.get('county') ?? '').trim() || undefined,
    countyFilters: parseCsvParam(sp.get('counties'), /^\d{5}$/, 50),
    targetLenderRef: parseTargetLenderRef(sp.get('target_lender_ref'), allowedLenderRefs),
    targetLenderRefs: allowedLenderRefs,
    portfolioCriteria: parsePortfolioCriteria(sp, allowedLenderRefs),
    approvalStatus: approvalStatus === 'any' ? undefined : approvalStatus,
    outreachStatus: outreachStatus === 'any' ? undefined : outreachStatus,
    assignedTo: (sp.get('assigned_to') ?? '').trim() || undefined,
    agedDays: Number(sp.get('aged_days') ?? '') || null,
    cohortId: (sp.get('cohort_id') ?? '').trim() || undefined,
    funnelStage: parseFunnelStage(sp.get('funnel_stage')),
  };
}

/**
 * The `/api/leads` request the queue sends for `sp`: `assigned_to=me`
 * resolves to `actorEmail` (undefined while it is unknown, which the caller
 * must treat as "not ready", exactly like the route).
 */
export function leadsRequestFromSearchParams(
  sp: URLSearchParams,
  allowedLenderRefs: readonly string[],
  actorEmail: string | null | undefined,
): LeadsRequest {
  const input = leadQueueFilterInputFromSearchParams(sp, allowedLenderRefs);
  const assignedTo = isAssignedToMe(input.assignedTo) ? actorEmail ?? undefined : input.assignedTo;
  return {
    segment: input.segment,
    geo: {
      state: input.stateFilter,
      zip: input.zipFilter,
      county: input.countyFilter,
      counties: input.countyFilters,
      states: input.stateFilters,
      zips: input.zipFilters,
      cities: input.cityFilters,
      borrowerIds: input.borrowerIdFilters,
    },
    opts: {
      segmentCodes: input.segmentCodes,
      segmentMode: input.segmentMode,
      targetLenderRef: input.targetLenderRef,
      cohortId: input.cohortId,
      funnelStage: input.funnelStage,
      portfolioCriteria: input.portfolioCriteria,
      approvalStatus: (input.approvalStatus ?? 'any') as ApprovalFilter,
      outreachStatus: (input.outreachStatus ?? 'any') as OutreachFilter,
      assignedTo,
      agedDays: input.agedDays,
    },
  };
}

/** True while `assigned_to=me` waits for the signed-in email (the queue reads nothing then). */
export function leadsRequestUnresolved(sp: URLSearchParams, actorEmail: string | null | undefined): boolean {
  return isAssignedToMe((sp.get('assigned_to') ?? '').trim()) && !actorEmail;
}
