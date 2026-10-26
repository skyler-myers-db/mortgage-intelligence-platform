/**
 * The query of an audit-free Lead Queue match count (W5e: the filter
 * omnibox, wow-power-6, and More like this, wow-power-2), as an ALLOWLIST of
 * the grammar / pattern keys in a fixed order: a count can never carry a
 * borrower list, an approval-request scope, a Genie cohort, an include_*
 * switch, a page size, a cursor, a sort or a Growth Agent proof, whatever
 * the caller hands in.
 *
 * Pure and import-free, so the lazy omnibox and More like this chunks build
 * it without reaching the transport (lib/apiClients/leadCount.ts owns the
 * read and is imported by their parents only: a lazy chunk that reaches the
 * initial transport chunk without api.ts splits that initial chunk in two).
 */
import type { LeadCountResponse } from '../types/leadFilters';

export const LEAD_COUNT_QUERY_KEYS = [
  'state',
  'states',
  'segment',
  'segment_codes',
  'segment_mode',
  'product',
  'lender_relationship',
  'approval_status',
  'outreach_status',
  'aged_days',
  'min_opportunity_score',
  'max_opportunity_score',
  'min_rate_spread_bps',
  'max_rate_spread_bps',
] as const;

/** The allowlisted, non-empty params of `params`, in LEAD_COUNT_QUERY_KEYS order. */
export function leadCountQuery(params: URLSearchParams): string {
  const query = new URLSearchParams();
  for (const key of LEAD_COUNT_QUERY_KEYS) {
    const value = params.get(key)?.trim();
    if (value) query.set(key, value);
  }
  return query.toString();
}

/** One count read for an allowlisted query: the TanStack options the parent builds (leadCountQueryOptions). */
export interface LeadCountQueryOptions {
  queryKey: readonly unknown[];
  queryFn: (context: { signal: AbortSignal }) => Promise<LeadCountResponse>;
}

export type LeadCountSource = (query: string) => LeadCountQueryOptions;
