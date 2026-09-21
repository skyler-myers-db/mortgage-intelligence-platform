/**
 * Pins the hygiene GATE: a test that did not opt out must fail.
 *
 * test.fail() here is not a known-defect placeholder. It is how Playwright
 * asserts that a test FAILS: each body below passes its own steps, so the only
 * thing that can fail it is the hygiene teardown in test.ts. If that gate ever
 * stops throwing, these "unexpectedly pass" and the suite goes red.
 *
 * Tracing is off for this file (it is a worker option, so it cannot be scoped
 * to a describe): with trace 'retain-on-failure', Playwright 1.59 stalls for
 * the whole test timeout while finalizing the trace of an unexpected pass,
 * which would turn the clear "Expected to fail, but passed." into a misleading
 * timeout for whoever broke the gate.
 */
import { test } from './test';

test.use({ trace: 'off' });

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
