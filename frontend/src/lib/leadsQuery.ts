import { api } from './api';
import { queryKeys } from './queryKeys';

/**
 * leadsQuery — ONE request object feeds both the `/api/leads` fetcher and its
 * TanStack query key, so the two cannot drift.
 *
 * Defect this closes (audit runtime-02 / tables-v1, 2026-09-21): the Lead
 * Queue listed its filters twice — once as `api.leadsPage` arguments and once
 * as a hand-written key array — and the key forgot `cities`. Every
 * `?cities=CITY~ST` drill-down therefore shared one cache entry with every
 * other city (and with the national queue), so for the 30 s staleTime the
 * table showed Chicago rows under a Springfield chip with no refetch.
 *
 * TanStack hashes plain-object keys deterministically (sorted property names,
 * `undefined` members dropped), so putting the request object itself in the
 * key is stable across renders and makes a forgotten member impossible: an
 * argument the fetcher can see is, by construction, an argument the key hashes.
 */

type LeadsPageArgs = Parameters<typeof api.leadsPage>;

/** Everything `api.leadsPage` accepts except the abort signal. */
export interface LeadsRequest {
  segment?: LeadsPageArgs[0];
  geo?: NonNullable<LeadsPageArgs[2]>;
  opts?: NonNullable<LeadsPageArgs[3]>;
}

/**
 * @param scope a per-surface discriminator (`'lead-queue'`,
 *   `'segment-intelligence'`) so two routes never share an entry by accident.
 * @param request the single source of truth for the fetch.
 * @param implicitInputs fingerprints of inputs the fetcher reads from OUTSIDE
 *   the request. Today that is only the Growth Agent cohort proof, which
 *   `api.leadsPage` parses from `window.location` when
 *   `opts.growthAgentProof` is undefined (parsing can throw on a malformed
 *   handoff, which must surface as a query error, not a render crash).
 */
export function leadsQuery(
  scope: string,
  request: LeadsRequest,
  implicitInputs: readonly string[] = [],
) {
  return {
    queryKey: queryKeys.leads([scope, request, ...implicitInputs]),
    fetcher: (signal: AbortSignal) =>
      api.leadsPage(request.segment, signal, request.geo, request.opts),
  };
}
