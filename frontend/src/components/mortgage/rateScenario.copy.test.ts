import { describe, expect, it } from 'vitest';
import { scenarioHeadline, scenarioValueText } from './rateScenario.copy';

describe('rateScenario.copy', () => {
  it('states today at step 0', () => {
    expect(
      scenarioHeadline({ step: 0, ratePct: 6.3, inTheMoney: 991_931, today: 991_931, contactable: 40_100, scopeName: null }),
    ).toBe("At today's 30-year par rate (6.30%), 991,931 borrowers clear this refresh's refi screen; 40,100 of them are contactable.");
    expect(scenarioValueText({ step: 0, ratePct: 6.3, inTheMoney: 991_931 })).toBe(
      "Par rate 6.30%, today's rate: 991,931 borrowers in the money.",
    );
  });

  it('states a fall as more than today', () => {
    expect(
      scenarioHeadline({ step: -50, ratePct: 5.8, inTheMoney: 1_204_331, today: 991_931, contactable: 46_100, scopeName: null }),
    ).toBe(
      "If the 30-year par rate were 0.50 points lower (5.80%), 1,204,331 borrowers would clear this refresh's refi screen: 212,400 more than today; 46,100 of them are contactable.",
    );
    expect(scenarioValueText({ step: -50, ratePct: 5.8, inTheMoney: 1_204_331 })).toBe(
      'Par rate 5.80%, down 50 basis points: 1,204,331 borrowers in the money.',
    );
  });

  it('states a rise as fewer than today, for a drilled state', () => {
    expect(
      scenarioHeadline({ step: 25, ratePct: 6.55, inTheMoney: 800, today: 1_000, contactable: 30, scopeName: 'Texas' }),
    ).toBe(
      "If the 30-year par rate were 0.25 points higher (6.55%), 800 borrowers in Texas would clear this refresh's refi screen: 200 fewer than today; 30 of them are contactable.",
    );
    expect(scenarioValueText({ step: 25, ratePct: 6.55, inTheMoney: 800 })).toBe(
      'Par rate 6.55%, up 25 basis points: 800 borrowers in the money.',
    );
  });

  it('drops the contactable clause when it is not reported, and says "the same" for no change', () => {
    expect(
      scenarioHeadline({ step: 75, ratePct: 7.05, inTheMoney: 1, today: 1, contactable: null, scopeName: null }),
    ).toBe(
      "If the 30-year par rate were 0.75 points higher (7.05%), 1 borrower would clear this refresh's refi screen: the same as today.",
    );
    expect(
      scenarioHeadline({ step: 0, ratePct: 6.3, inTheMoney: 12, today: 12, contactable: null, scopeName: null }),
    ).toBe("At today's 30-year par rate (6.30%), 12 borrowers clear this refresh's refi screen.");
  });
});
