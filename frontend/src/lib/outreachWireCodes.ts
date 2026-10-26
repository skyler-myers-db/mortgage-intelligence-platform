/**
 * The closed approve / reject code vocabularies the outreach routes accept
 * (audit quality-04 P2, W5c w5-lead-queue-paging cut 4). The server answers
 * a 422 to any other offer or reason code, so a decision carrying one is
 * refused here, before any network I/O: no DRAFT_OUTREACH row is written for
 * an approve that could never be recorded, and nothing sends null in place
 * of an unknown code.
 *
 * Each list equals its generated wire union (wireContract.leads.check.ts
 * proves it both ways). The refusal names the side and the field only:
 * never the code itself, which stays out of toasts and telemetry.
 */
import type { ApiRequest } from '../types/api.gen';

export type WireOfferCode = NonNullable<ApiRequest<'OutreachApproveRequest'>['offer_code']>;
export type WireRejectReasonCode = ApiRequest<'OutreachRejectRequest'>['rationale_code'];

export const WIRE_OFFER_CODES = [
  'refi',
  'heloc',
  'cash_out',
  'purchase',
  'retention',
  'recapture',
  'refi_plus_heloc',
  'investor',
  'nurture',
] as const satisfies readonly WireOfferCode[];

export const WIRE_REJECT_REASON_CODES = [
  'out_of_footprint',
  'do_not_call',
  'opt_out',
  'fair_lending_review',
  'low_intent',
  'data_quality',
  'other_with_text',
] as const satisfies readonly WireRejectReasonCode[];

const OFFER_CODES: ReadonlySet<string> = new Set(WIRE_OFFER_CODES);
const REJECT_REASON_CODES: ReadonlySet<string> = new Set(WIRE_REJECT_REASON_CODES);

export function isWireOfferCode(value: string): value is WireOfferCode {
  return OFFER_CODES.has(value);
}

export function isRejectReasonCode(value: string): value is WireRejectReasonCode {
  return REJECT_REASON_CODES.has(value);
}

export type WireCodeSide = 'approve' | 'reject';
export type WireCodeField = 'offer_code' | 'rationale_code';

/** The Lead Queue's approve copy for a refused code: the route's own 422 wording. */
export const RELOAD_TO_APPROVE = 'Reload the app to approve';

/**
 * A decision named a code the route would refuse; nothing was sent. Its
 * message is the generic copy a surface without its own mapping shows.
 */
export class WireCodeRefusedError extends Error {
  readonly side: WireCodeSide;
  readonly field: WireCodeField;

  constructor(side: WireCodeSide, field: WireCodeField) {
    super('Reload the app, then try again.');
    this.name = 'WireCodeRefusedError';
    this.side = side;
    this.field = field;
  }
}

/**
 * The offer code a decision may send: null stays null (an absent code is
 * sent as absent, as before), a reviewed code passes, anything else throws.
 */
export function wireOfferCodeOrNull(value: string | null | undefined, side: WireCodeSide): WireOfferCode | null {
  if (value === null || value === undefined) return null;
  if (isWireOfferCode(value)) return value;
  throw new WireCodeRefusedError(side, 'offer_code');
}

/** The reject reason a decision may send, or a refusal. */
export function wireRejectReasonCode(value: string): WireRejectReasonCode {
  if (isRejectReasonCode(value)) return value;
  throw new WireCodeRefusedError('reject', 'rationale_code');
}
