import { describe, expect, it } from 'vitest';
import { DEFAULT_CAMPAIGN_SETUP } from './portfolio-builder.logic';
import { campaignSetupsEqual, portfolioUnsavedMessage } from './portfolio-builder.unsaved';

describe('Portfolio Builder unsaved work (audit states-05)', () => {
  it('compares campaign setups by value, so typing a value back is clean again', () => {
    const edited = { ...DEFAULT_CAMPAIGN_SETUP, budget: '25000' };
    expect(campaignSetupsEqual(edited, DEFAULT_CAMPAIGN_SETUP)).toBe(false);
    expect(campaignSetupsEqual({ ...edited, budget: DEFAULT_CAMPAIGN_SETUP.budget }, DEFAULT_CAMPAIGN_SETUP)).toBe(true);
    expect(campaignSetupsEqual({ ...DEFAULT_CAMPAIGN_SETUP, marketHouseholdTogether: true }, DEFAULT_CAMPAIGN_SETUP)).toBe(false);
  });

  it('names what would be lost, or nothing when nothing is unsaved', () => {
    expect(portfolioUnsavedMessage(false, false)).toBeNull();
    expect(portfolioUnsavedMessage(true, false)).toBe('Your filter changes have not been run. Leaving discards them.');
    // critic-v3: the setup is kept as this tab's draft, so leaving does not discard it.
    expect(portfolioUnsavedMessage(false, true)).toBe(
      'Your campaign setup has not been saved with a build. It is kept as a draft in this tab until you save or reset it.',
    );
    expect(portfolioUnsavedMessage(true, true)).toBe(
      'Your filter changes have not been run and will be discarded. Your campaign setup has not been saved with a build; it is kept as a draft in this tab until you save or reset it.',
    );
    for (const setupOnly of [portfolioUnsavedMessage(false, true), portfolioUnsavedMessage(true, true)]) {
      expect(setupOnly).not.toMatch(/discards (it|both)/);
    }
  });
});
