/**
 * Module 0 — accessibility smoke test.
 *
 * Runs axe-core against the Home route (the highest-traffic surface + the
 * one stakeholders land on first). Asserts zero `serious` or `critical`
 * violations; `moderate` / `minor` are logged as TODOs but do not fail
 * the test (out of scope for this pass — surfacing is the value).
 *
 * Gated on `E2E_LIVE=1` to mirror real_data.spec.ts: the backend requires
 * live Databricks credentials to boot post-cutover (backend/runtime.py
 * ::_preflight_credentials), so PR CI can't stand up a real uvicorn + vite
 * pair. Nightly runs this against the localhost webServer booted with
 * real creds.
 *
 * Why not a deployed-URL run: the Databricks Apps OAuth proxy 302s
 * headless browsers to a consent page. The nightly workflow wires
 * DATABRICKS_TOKEN through extraHTTPHeaders, which works for XHR but
 * not for the initial document load in all browsers reliably. Keeping
 * the test on localhost (same code, real backends) sidesteps the
 * OAuth-flow flakiness.
 */
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run accessibility smoke against the live app.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
test.use({ baseURL: APP_URL });

/**
 * Every public route + the deep-link variants that carry an id. We run
 * axe against each so a11y regressions are caught wherever they land,
 * not only on the home hero.
 *
 * `borrower-360` and `offer-orchestrator` now redirect to /lead-queue
 * when no id is present, so we use /lead-queue as a known-good
 * jumping-off point to pick a real id before visiting those routes.
 */
const ROUTES: Array<{ path: string; readySelector?: RegExp | string }> = [
  { path: '/',                       readySelector: /Who should we contact, why now, and with what offer\?/i },
  { path: '/portfolio-builder',      readySelector: /Build a high-intent borrower population/i },
  { path: '/segment-intelligence',   readySelector: /borrower segment/i },
  { path: '/lead-queue',             readySelector: /Lead queue|Ranked borrower queue/i },
  { path: '/ask-genie',              readySelector: /Ask a question|Ask Genie/i },
  { path: '/outreach-composer',      readySelector: /Outreach/i },
  { path: '/admin-config',           readySelector: /Lender|Admin|Config/i },
];

async function runAxeAndAssertClean(page: import('@playwright/test').Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    // Canvas decorative background layer — documented waiver in the
    // original Home-only spec. Applies to every route since DataMesh
    // lives in the AppShell.
    .exclude('canvas')
    .analyze();

  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  );
  const moderate = results.violations.filter((v) => v.impact === 'moderate');
  const minor = results.violations.filter((v) => v.impact === 'minor');
  if (moderate.length || minor.length) {
    // eslint-disable-next-line no-console
    console.log(
      `[a11y TODO] ${label}: ${moderate.length} moderate + ${minor.length} minor.\n` +
        [...moderate, ...minor]
          .map(
            (v) =>
              `  - [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node${v.nodes.length === 1 ? '' : 's'})`,
          )
          .join('\n'),
    );
  }

  expect(
    serious,
    `${label}: axe-core found ${serious.length} serious/critical violation(s):\n` +
      serious
        .map(
          (v) =>
            `  - [${v.impact}] ${v.id}: ${v.help}\n    nodes: ${v.nodes
              .map((n) => n.target.join(' '))
              .join(', ')}\n    help: ${v.helpUrl}`,
        )
        .join('\n'),
  ).toEqual([]);
}

test.describe('Module 0 — accessibility (nightly)', () => {
  for (const route of ROUTES) {
    test(`${route.path} has zero serious/critical axe violations`, async ({ page }) => {
      await page.goto(route.path);
      if (route.readySelector) {
        await expect(
          page.getByText(route.readySelector).first(),
        ).toBeVisible({ timeout: 20_000 });
      }
      await runAxeAndAssertClean(page, route.path);
    });
  }

  test('borrower-360 (deep-linked real id) has zero serious/critical violations', async ({ page }) => {
    await page.goto('/lead-queue');
    // Grab the first real borrower id from the queue so the deep-link test
    // never falls back to a fixture id.
    const firstRow = page.locator('tbody tr').first();
    await expect(firstRow).toBeVisible({ timeout: 30_000 });
    const href = await firstRow.locator('a[href*="/borrower-360/"]').first().getAttribute('href');
    if (!href) test.skip(true, 'No borrower id available to deep-link.');
    await page.goto(href!);
    await expect(page.getByText(/Customer 360|Why we recommend/i)).toBeVisible({ timeout: 20_000 });
    await runAxeAndAssertClean(page, '/borrower-360/:id');
  });

  test('offer-orchestrator (deep-linked real id) has zero serious/critical violations', async ({ page }) => {
    await page.goto('/lead-queue');
    const firstRow = page.locator('tbody tr').first();
    await expect(firstRow).toBeVisible({ timeout: 30_000 });
    const href = await firstRow.locator('a[href*="/borrower-360/"]').first().getAttribute('href');
    if (!href) test.skip(true, 'No borrower id available to deep-link.');
    const id = href!.split('/').pop();
    await page.goto(`/offer-orchestrator/${id}`);
    await expect(page.getByText(/Draft outreach|Recommended offer/i)).toBeVisible({ timeout: 30_000 });
    await runAxeAndAssertClean(page, '/offer-orchestrator/:id');
  });
});
