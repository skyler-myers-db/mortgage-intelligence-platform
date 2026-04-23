/**
 * R5-22 demonstration: test files are allowed to import from src/mocks.
 * This file is under `src/routes/**` (which would normally be banned
 * from mocks imports) but has the `*.test.ts` suffix, matching the
 * `ignores` list in frontend/eslint.config.js. If this file ever fails
 * lint, the mocks-ban ignore pattern has regressed and test fixtures
 * are now tangled with the production ban. Delete this file once the
 * guard is proven — it's a one-shot canary, not an ongoing test.
 */
import { mockPortfolio } from '../../mocks/fixtureData';
import { describe, expect, it } from 'vitest';

describe('mock leak guard (R5-22 canary)', () => {
  it('allows test files to import from src/mocks', () => {
    expect(typeof mockPortfolio.marketable_population).toBe('number');
  });
});
