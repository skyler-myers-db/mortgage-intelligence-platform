import { describe, expect, it } from 'vitest';
import {
  isRejectReasonCode,
  isWireOfferCode,
  WIRE_OFFER_CODES,
  WIRE_REJECT_REASON_CODES,
  WireCodeRefusedError,
  wireOfferCodeOrNull,
  wireRejectReasonCode,
} from './outreachWireCodes';

describe('outreach wire code vocabularies (quality-04 P2)', () => {
  it('lists the nine reviewed offer codes and the seven reject reasons, once each', () => {
    expect(WIRE_OFFER_CODES).toHaveLength(9);
    expect(new Set(WIRE_OFFER_CODES).size).toBe(9);
    expect(WIRE_REJECT_REASON_CODES).toHaveLength(7);
    expect(new Set(WIRE_REJECT_REASON_CODES).size).toBe(7);
  });

  it('guards each vocabulary exactly', () => {
    for (const code of WIRE_OFFER_CODES) expect(isWireOfferCode(code)).toBe(true);
    for (const code of WIRE_REJECT_REASON_CODES) expect(isRejectReasonCode(code)).toBe(true);
    for (const stray of ['mystery_offer', '', 'REFI', 'refi ', 'low_intent']) expect(isWireOfferCode(stray)).toBe(false);
    for (const stray of ['vibes', '', 'refi', 'Other_with_text']) expect(isRejectReasonCode(stray)).toBe(false);
  });

  it('keeps an absent offer code absent and passes a reviewed one', () => {
    expect(wireOfferCodeOrNull(null, 'approve')).toBeNull();
    expect(wireOfferCodeOrNull(undefined, 'reject')).toBeNull();
    expect(wireOfferCodeOrNull('heloc', 'approve')).toBe('heloc');
    expect(wireRejectReasonCode('do_not_call')).toBe('do_not_call');
  });

  it('refuses an unknown code with the side and field, never the value', () => {
    const refusals: unknown[] = [];
    for (const run of [
      () => wireOfferCodeOrNull('mystery_offer', 'approve'),
      () => wireOfferCodeOrNull('', 'reject'),
      () => wireRejectReasonCode('mystery_reason'),
    ]) {
      try {
        run();
      } catch (err) {
        refusals.push(err);
      }
    }
    expect(refusals).toHaveLength(3);
    const [approve, rejectOffer, rejectReason] = refusals as WireCodeRefusedError[];
    expect(approve).toBeInstanceOf(WireCodeRefusedError);
    expect([approve.side, approve.field, approve.message]).toEqual(['approve', 'offer_code', 'Reload the app, then try again.']);
    expect([rejectOffer.side, rejectOffer.field]).toEqual(['reject', 'offer_code']);
    expect([rejectReason.side, rejectReason.field, rejectReason.message]).toEqual(['reject', 'rationale_code', 'Reload the app, then try again.']);
    for (const err of refusals as Error[]) {
      expect(err.message).not.toMatch(/mystery/);
      expect(JSON.stringify(err)).not.toMatch(/mystery/);
      expect(Object.values(err)).not.toContain('mystery_offer');
    }
  });
});
