/**
 * "More like this" (wow-power-2, W5e item 4a): the reviewed pattern of one
 * borrower as Lead Queue filter params. Deterministic, pure, and built from
 * REVIEWED attributes only:
 *
 *   segment              the primary segment (segment_codes[0]) when the
 *                        queue's segment filter knows it
 *   product              the recommended offer: refi -> Refi, heloc -> HELOC,
 *                        cash_out -> Cash-out, purchase -> Purchase,
 *                        retention -> Retention (refi_plus_heloc and an
 *                        unknown code add none)
 *   state                the borrower's state (a USPS code)
 *   lender_relationship  current customer, else former customer, else a
 *                        competitor lien (first true); none -> omitted
 *   score band           floor(score / 10) * 10 .. +9, clamped to 0..100
 *
 * Never sub-state geography (ZIP, city, county, CBSA), never a demographic
 * or proxy field, never a borrower id: a pattern is a cohort a compliance
 * reviewer can read, not a lookalike model.
 */
import type { LeadSummary } from '../types';
import { SEGMENT_DEFINITIONS } from './segmentMetadata';
import { USPS_STATE_CODES } from './uspsStates';

export interface LeadPatternChip {
  label: string;
  value: string;
}

export interface LeadPattern {
  params: URLSearchParams;
  chips: LeadPatternChip[];
}

/** The only keys a pattern ever carries. */
export const LEAD_PATTERN_KEYS = [
  'segment',
  'product',
  'state',
  'lender_relationship',
  'min_opportunity_score',
  'max_opportunity_score',
] as const;

const PRODUCT_OF_OFFER: Readonly<Record<string, string>> = {
  refi: 'Refi',
  heloc: 'HELOC',
  cash_out: 'Cash-out',
  purchase: 'Purchase',
  retention: 'Retention',
};
const SEGMENT_NAME = new Map<string, string>(SEGMENT_DEFINITIONS.map((definition) => [definition.code, definition.name]));

function relationshipOf(lead: LeadSummary): string | null {
  if (lead.is_current_customer === true) return 'Current customer';
  if (lead.is_former_customer === true) return 'Former customer';
  if (lead.is_competitor_lien === true) return 'Competitor customer';
  return null;
}

export function leadPattern(lead: LeadSummary): LeadPattern {
  const params = new URLSearchParams();
  const chips: LeadPatternChip[] = [];
  const segment = lead.segment_codes[0];
  const segmentName = segment ? SEGMENT_NAME.get(segment) : undefined;
  if (segment && segmentName) {
    params.set('segment', segment);
    chips.push({ label: 'SEGMENT', value: segmentName });
  }
  const product = PRODUCT_OF_OFFER[(lead.recommended_offer_code ?? '').toLowerCase()];
  if (product) {
    params.set('product', product);
    chips.push({ label: 'PRODUCT', value: product });
  }
  const state = (lead.state ?? '').trim().toUpperCase();
  if (USPS_STATE_CODES.has(state)) {
    params.set('state', state);
    chips.push({ label: 'STATE', value: state });
  }
  const relationship = relationshipOf(lead);
  if (relationship) {
    params.set('lender_relationship', relationship);
    chips.push({ label: 'RELATIONSHIP', value: relationship });
  }
  if (Number.isFinite(lead.opportunity_score)) {
    const low = Math.min(100, Math.max(0, Math.floor(lead.opportunity_score / 10) * 10));
    const high = Math.min(100, low + 9);
    params.set('min_opportunity_score', String(low));
    params.set('max_opportunity_score', String(high));
    chips.push({ label: 'SCORE', value: `${low}–${high}` });
  }
  return { params, chips };
}
