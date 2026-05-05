/**
 * Module 0 — accessibility smoke test.
 *
 * Runs axe-core against every public route in BOTH dark and light themes.
 * Asserts zero `serious` or `critical` violations; `moderate` / `minor`
 * are logged as TODOs but do not fail the test.
 *
 * Why both themes: a prior audit (2026-04-22) found 5 light-theme-only
 * contrast blockers that dark-only runs would never catch. The
 * contrast-regression guard recommended in that audit lives here. Flip
 * `data-theme` by setting localStorage + document root attribute before
 * each axe scan; AppContext reads the same keys on boot.
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
import { test, expect, type APIRequestContext } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run accessibility smoke against the live app.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};
test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

async function fetchFirstLeadId(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=1`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
  const id = rows[0]?.borrower_id;
  expect(id, 'need a live borrower id for a11y deep-link coverage').toBeTruthy();
  return id!;
}

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
  { path: '/portfolio-builder',      readySelector: /Build a borrower population/i },
  { path: '/segment-intelligence',   readySelector: /borrower segment/i },
  { path: '/lead-queue',             readySelector: /Lead queue|Ranked borrower queue/i },
  { path: '/ask-genie',              readySelector: /Ask a question|Ask Genie/i },
  { path: '/offer-orchestrator',     readySelector: /Offer Orchestrator|Choose a borrower/i },
  { path: '/admin-config',           readySelector: /Lender|Admin|Config/i },
];

type Theme = 'dark' | 'light';
const THEMES: Theme[] = ['dark', 'light'];

/**
 * Flip the app's theme in-place without a full reload. AppContext reads
 * `mip.theme` from localStorage on boot and mirrors it into
 * <html data-theme="…">. We set both so tokens flip immediately AND the
 * next route navigation hydrates with the same theme.
 */
async function setTheme(page: import('@playwright/test').Page, theme: Theme) {
  await page.evaluate((t: Theme) => {
    try {
      window.localStorage.setItem('mip.theme', t);
    } catch {
      // ignore (SSR / private mode) — attribute fallback below
    }
    document.documentElement.setAttribute('data-theme', t);
  }, theme);
}

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
  for (const theme of THEMES) {
    test.describe(`theme=${theme}`, () => {
      for (const route of ROUTES) {
        test(`${route.path} has zero serious/critical axe violations (${theme})`, async ({ page }) => {
          // Seed the theme in localStorage BEFORE the first paint so
          // AppContext boots into the right palette and no flash occurs.
          await page.addInitScript((t) => {
            try {
              window.localStorage.setItem('mip.theme', t as string);
            } catch {
              // ignore
            }
          }, theme);
          await page.goto(route.path);
          // Defensive: also flip the attribute post-load in case the
          // init script raced a browser-specific storage quirk.
          await setTheme(page, theme);
          if (route.readySelector) {
            await expect(
              page.getByText(route.readySelector).first(),
            ).toBeVisible({ timeout: 20_000 });
          }
          await runAxeAndAssertClean(page, `${route.path} [${theme}]`);
        });
      }

      test(`borrower-360 (deep-linked real id) has zero serious/critical violations (${theme})`, async ({ page, request }) => {
        const id = await fetchFirstLeadId(request);
        await page.addInitScript((t) => {
          try {
            window.localStorage.setItem('mip.theme', t as string);
          } catch {
            // ignore
          }
        }, theme);
        await page.goto(`/borrower-360/${id}`);
        await setTheme(page, theme);
        await expect(page.getByText(/Customer 360|Why we recommend/i).first()).toBeVisible({ timeout: 20_000 });
        await runAxeAndAssertClean(page, `/borrower-360/:id [${theme}]`);
      });

      test(`offer-orchestrator (deep-linked real id) has zero serious/critical violations (${theme})`, async ({ page, request }) => {
        const id = await fetchFirstLeadId(request);
        await page.addInitScript((t) => {
          try {
            window.localStorage.setItem('mip.theme', t as string);
          } catch {
            // ignore
          }
        }, theme);
        await page.goto(`/offer-orchestrator/${id}`);
        await setTheme(page, theme);
        await expect(page.getByText(/Draft outreach|Recommended offer/i).first()).toBeVisible({ timeout: 30_000 });
        await runAxeAndAssertClean(page, `/offer-orchestrator/:id [${theme}]`);
      });
    });
  }
});
