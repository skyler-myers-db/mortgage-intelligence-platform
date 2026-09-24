import { describe, expect, it } from 'vitest';
import { genieClearance } from './offer-orchestrator.genie-clearance';

/**
 * The reach the decision bar reserves for the docked Genie panel. The
 * rendered proof (Approve and Reject uncovered with the panel open, with and
 * without the Console) is in tests/e2e/fixture/offer-orchestrator.fixture.spec.ts.
 */
describe('genieClearance', () => {
  const bar = { left: 88, right: 1416 };

  it('reserves how far the docked panel reaches into the bar', () => {
    // 1440 wide, Console closed: the 420px panel ends 16px from the edge.
    expect(genieClearance(bar, { left: 1004, right: 1424 })).toBe(412);
    expect(genieClearance(bar, { left: 1004.4, right: 1424 })).toBe(412);
  });

  it('reserves nothing for a panel beside or past the bar', () => {
    expect(genieClearance(bar, { left: 1416, right: 1836 })).toBe(0);
    expect(genieClearance(bar, { left: 0, right: 88 })).toBe(0);
  });

  it('reserves nothing when the panel covers most of the bar: padding cannot clear a sheet', () => {
    const narrow = { left: 0, right: 700 };
    expect(genieClearance(narrow, { left: 264, right: 684 })).toBe(0);
    expect(genieClearance(narrow, { left: 300, right: 684 })).toBe(400);
    expect(genieClearance({ left: 10, right: 10 }, { left: 0, right: 20 })).toBe(0);
  });
});
