/**
 * Pins the hygiene GATE: a test that did not opt out must fail.
 *
 * test.fail() here is not a known-defect placeholder. It is how Playwright
 * asserts that a test FAILS: each body below passes its own steps, so the only
 * thing that can fail it is the hygiene teardown in test.ts. If that gate ever
 * stops throwing, these "unexpectedly pass" and the suite goes red.
 *
 * The expected failure is cheap: the harness discards the browser trace when
 * a test's outcome matches its expectation (`failureTrace` in test.ts), so
 * these do not pay for trace finalization.
 */
import { test } from './test';

test.describe('hygiene gate fails a test that did not opt out', () => {
  test('on a console.error', async ({ app, page }) => {
    test.fail();
    await app.gotoRoute('/glossary');
    await page.evaluate(() => console.error('fixture self-test: the gate must fail this test'));
  });

  test('on an API call with no registered fixture', async ({ app, page }) => {
    test.fail();
    await app.gotoRoute('/glossary');
    await page.evaluate(() => fetch('/api/v1/not-a-fixture').then(() => undefined));
  });
});
