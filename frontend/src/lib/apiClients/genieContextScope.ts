import type { ApiRequest } from '../../types/api.gen';
import { postJson } from '../apiTransport';

/**
 * The Genie context-scope render (audit 2026-09-21 `genie-04`, phase 2).
 *
 * The browser sends a closed context of CODES (segment codes, a USPS code, a
 * county FIPS, a ZIP, a governed `CITY~ST` key, a Competitor ref, assignee
 * 'me') and the server renders the reviewed, guard-clean question, or names
 * the closed reason none is offered. `POST /api/genie/context-scope` reads no
 * borrower data and writes no audit row; the caller prefills the Genie
 * composer with the prompt and never submits it.
 *
 * Deliberately NOT spread into `api` (lib/api.ts is in the initial closure):
 * only the lazy Lead Queue empty state imports this module. Types mirror
 * backend/schemas/genie_context.py, pinned by wireContract.genie.check.ts.
 */

export type GenieContextSegmentCode =
  | 'itm'
  | 'listed'
  | 'permit'
  | 'investor'
  | 'equity'
  | 'retention'
  | 'second_lien_itm'
  | 'heloc_draw_to_payback'
  | 'home_equity_history'
  | 'refi_propensity'
  | 'itm_on_related_property'
  | 'payoff_loss_leads'
  | 'permit_activity';

/** The 50 states plus DC by USPS code: the server enum, never a looser string. */
export type GenieContextStateCode = NonNullable<ApiRequest<'GenieContext'>['state']>;

export interface GenieContext {
  route: 'lead-queue';
  segment_codes?: GenieContextSegmentCode[];
  segment_mode?: 'any' | 'all';
  state?: GenieContextStateCode | null;
  /** Five digits. */
  county_fips?: string | null;
  /** Five digits. */
  zip?: string | null;
  /** `CITY~ST`. */
  city?: string | null;
  /** `Competitor X` or `Competitor Other`. */
  lender_ref?: string | null;
  assignee?: 'me' | null;
}

export type GenieContextWithheld = 'unsupported_combination' | 'excluded_value' | 'place_unavailable' | 'refused';

export interface GenieContextScopeResponse {
  prompt: string | null;
  withheld: GenieContextWithheld | null;
  omitted: 'assignee'[];
}

const GENIE_CONTEXT_SCOPE_PATH = '/api/genie/context-scope';

export function requestGenieContextScope(body: GenieContext, signal?: AbortSignal): Promise<GenieContextScopeResponse> {
  return postJson<GenieContextScopeResponse, GenieContext>(GENIE_CONTEXT_SCOPE_PATH, body, signal);
}
