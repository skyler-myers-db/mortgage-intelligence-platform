/**
 * Runs ONLY inside the nested Playwright invocation that
 * runner.fixture.spec.ts spawns (E2E_FIXTURE=1 E2E_FIXTURE_NESTED=1). It is
 * never collected by the fixture suite itself (`*.fixture.spec.ts`) or by
 * any other mode (`*.spec.ts`), because it fails on purpose: the outer test
 * asserts HOW it fails (its own error only, no spurious timeout, the run
 * terminates, the failure trace is attached).
 *
 * It needs no web server: the page is rendered with setContent, and the
 * attachment over 64 KiB is the shape that stalled Playwright's built-in
 * trace merge on Node 26 (see `failureTrace` in ../test.ts). The bytes are
 * random on purpose: trace zips deflate their entries, and the 64 KiB
 * threshold applies to the stored size, so a compressible buffer would not
 * reproduce the stall.
 */
import { randomBytes } from 'node:crypto';
import { expect, test } from '../test';
import { LARGE_ATTACHMENT_BYTES, NESTED_FAILURE_MESSAGE, NESTED_TEST_TITLE } from './contract';

test(NESTED_TEST_TITLE, async ({ page }, testInfo) => {
  await page.setContent('<main id="main-content"><h1>Nested harness probe</h1></main>');
  await testInfo.attach('large-attachment', {
    body: randomBytes(LARGE_ATTACHMENT_BYTES),
    contentType: 'application/octet-stream',
  });
  expect(1, NESTED_FAILURE_MESSAGE).toBe(2);
});
