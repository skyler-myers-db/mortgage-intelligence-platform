/**
 * Lead queue and borrower 360 endpoint clients: the ranked lead page (with
 * its growth-agent cohort proof verification), the borrower dossier reads,
 * and the next-best-offer recommendation.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  Borrower360,
  BorrowerProof,
  BorrowerLifecycle,
  LeadSummary,
  OfferRecommendation,
} from '../../types';
import type { LeadQueryOptions } from '../apiTypes';
import {
  _growthAgentProofFromLocation,
  _verifyGrowthAgentCohort,
  getJson,
  getJsonWithHeaders,
  postJson,
} from '../apiTransport';

export const leadsPageApi = {
  leadsPage: (
    segment?: string,
    signal?: AbortSignal,
    geo?: { state?: string; zip?: string; county?: string; counties?: string[]; states?: string[]; zips?: string[]; cities?: string[]; borrowerIds?: string[] },
    opts: LeadQueryOptions = {},
  ) => {
    // 2026-05-04 FIX β: forward state + zip to the API so the backend
    // queries borrower_360 directly (no score floor) when a geo
    // narrowing is requested. Without this, narrow ZIP/state filters
    // returned 0 rows because the unfiltered top-500 from
    // lead_population didn't include the geo's borrowers.
    const params = new URLSearchParams();
    const growthAgentProof = opts.growthAgentProof === undefined
      ? _growthAgentProofFromLocation()
      : opts.growthAgentProof;
    if (segment) params.set('segment', segment);
    if (opts.segmentCodes && opts.segmentCodes.length > 0) {
      params.set('segment_codes', opts.segmentCodes.join(','));
      params.set('segment_mode', opts.segmentMode ?? 'any');
    }
    if (opts.funnelStage) params.set('funnel_stage', opts.funnelStage);
    if (opts.limit) params.set('limit', String(opts.limit));
    if (geo?.state) params.set('state', geo.state);
    if (geo?.zip) params.set('zip', geo.zip);
    if (geo?.county) params.set('county', geo.county);
    if (geo?.counties && geo.counties.length > 0) params.set('counties', geo.counties.join(','));
    if (geo?.states && geo.states.length > 0) params.set('states', geo.states.join(','));
    if (geo?.zips && geo.zips.length > 0) params.set('zips', geo.zips.join(','));
    // `CITY~ST` pairs. `~` is unreserved, so it survives the encode literal.
    if (geo?.cities && geo.cities.length > 0) params.set('cities', geo.cities.join(','));
    if (geo?.borrowerIds && geo.borrowerIds.length > 0) params.set('borrower_ids', geo.borrowerIds.join(','));
    if (opts.targetLenderRef) params.set('target_lender_ref', opts.targetLenderRef);
    if (opts.cohortId) params.set('cohort_id', opts.cohortId);
    if (opts.approvalStatus && opts.approvalStatus !== 'any') params.set('approval_status', opts.approvalStatus);
    if (opts.outreachStatus && opts.outreachStatus !== 'any') params.set('outreach_status', opts.outreachStatus);
    if (opts.assignedTo) params.set('assigned_to', opts.assignedTo);
    if (opts.agedDays) params.set('aged_days', String(opts.agedDays));
    if (growthAgentProof) {
      params.set('include_identity_proof', 'true');
      params.set('growth_handoff', growthAgentProof.growthHandoff);
    }
    if (opts.portfolioCriteria) {
      for (const [key, value] of Object.entries(opts.portfolioCriteria)) {
        if (value !== null && value !== undefined && String(value).length > 0) {
          params.set(key, String(value));
        }
      }
    }
    const qs = params.toString();
    return getJsonWithHeaders<LeadSummary[]>(
      qs ? `/api/leads?${qs}` : '/api/leads',
      signal,
    ).then(async ({ data, headers }) => ({
      leads: data,
      totalMatching: headers.get('X-Total-Matching') ? Number(headers.get('X-Total-Matching')) : null,
      rankedMatching: headers.get('X-Ranked-Matching') ? Number(headers.get('X-Ranked-Matching')) : null,
      returnedRows: headers.get('X-Returned-Rows') ? Number(headers.get('X-Returned-Rows')) : null,
      truncatedAt: headers.get('X-Truncated-At') ? Number(headers.get('X-Truncated-At')) : null,
      growthAgentVerification: growthAgentProof
        ? await _verifyGrowthAgentCohort(headers, growthAgentProof)
        : null,
    }));
  },
};

export const borrowerApi = {
  borrower: (id: string, signal?: AbortSignal, fresh = false) =>
    getJson<Borrower360>(
      `/api/borrowers/${id}${fresh ? '?fresh=true' : ''}`,
      signal,
    ),

  borrowerProof: (id: string, signal?: AbortSignal) =>
    getJson<BorrowerProof>(`/api/borrowers/${id}/proof`, signal),

  borrowerLifecycle: (id: string, signal?: AbortSignal) =>
    getJson<BorrowerLifecycle>(`/api/borrowers/${id}/lifecycle`, signal),

  borrowerSearch: (q: string, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    params.set('q', q);
    return getJson<LeadSummary[]>(`/api/borrowers/search?${params.toString()}`, signal);
  },

  recommendOffer: (borrower_id: string, signal?: AbortSignal) =>
    postJson<OfferRecommendation, { borrower_id: string }>(
      '/api/offers/recommend',
      { borrower_id },
      signal,
    ),
};
