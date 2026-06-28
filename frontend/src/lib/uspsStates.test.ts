import { describe, expect, it } from 'vitest';
import { isUspsStateCode, USPS_STATE_CODES } from './uspsStates';

describe('USPS state-code guard', () => {
  it('accepts the 50 states plus DC only', () => {
    expect(USPS_STATE_CODES.size).toBe(51);
    for (const code of ['AL', 'CA', 'DC', 'IL', 'NY', 'TX', 'WA']) {
      expect(isUspsStateCode(code)).toBe(true);
      expect(isUspsStateCode(code.toLowerCase())).toBe(true);
    }
  });

  it('rejects territories, free text, and malformed tokens', () => {
    for (const code of ['PR', 'GU', 'VI', 'AS', 'MP', 'Illinois', 'I', 'ILL', '']) {
      expect(isUspsStateCode(code)).toBe(false);
    }
  });
});
