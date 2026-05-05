import { describe, expect, it } from 'vitest';
import { isGenieFollowUpQuestion } from './genieSession';

describe('isGenieFollowUpQuestion', () => {
  it('keeps contextual follow-ups in the active Genie conversation', () => {
    expect(isGenieFollowUpQuestion('why?')).toBe(true);
    expect(isGenieFollowUpQuestion('only Texas')).toBe(true);
    expect(isGenieFollowUpQuestion('show that by ZIP')).toBe(true);
  });

  it('starts clean conversations for standalone executive questions', () => {
    expect(isGenieFollowUpQuestion('Which ZIPs have the most in-the-money refinance candidates?')).toBe(false);
    expect(isGenieFollowUpQuestion('Break down in-the-money borrowers by state and return the count as a table.')).toBe(false);
  });
});
