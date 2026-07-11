/**
 * S4 — personalized "since your last login" home summary.
 *
 * Acceptance: the UI numbers are cross-checked against the API response.
 *
 * Two modes, mirroring the module0/real_data split:
 *  - Pinned mode (default, PR CI): the summary API response is fulfilled
 *    with a fixture and the spec asserts the rendered sentence carries
 *    EXACTLY the response's display tokens, and that clicking a number
 *    opens the EvidenceDrawer citing the snapshot table + metric view.
 *  - Live mode (E2E_LIVE=1, nightly): the spec GETs the real
 *    /api/v1/home/summary and asserts the deployed UI renders exactly the
 *    numbers the API returned — first-visit / no-baseline states included
 *    (welcome copy, no fake deltas).
 */
import { test, expect, type Page } from '@playwright/test';

const API_PREFIX = /\/api\/(?:v1\/)?/;
const apiPattern = (path: string) => new RegExp(`${API_PREFIX.source}${path}`);

const LIVE = process.env.E2E_LIVE === '1';
const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

interface SummaryHighlight {
  measure: string;
  label: string;
  display: string;
  value_token: string;
  current: number;
  baseline: number | null;
  delta: number | null;
  delta_pct: number | null;
}

interface SummaryPayload {
  status: 'delta' | 'first_visit' | 'no_baseline';
  previous_visit_at: string | null;
  baseline_snapshot_at: string | null;
  headline: string;
  phrasing_source: 'deterministic' | 'genie';
  phrasing_fallback_reason: string | null;
  highlights: SummaryHighlight[];
  current: Record<string, number | null>;
  baseline: Record<string, number | null> | null;
  deltas: Record<string, number | null> | null;
  current_source: string;
  baseline_source: string;
}

const PINNED_SUMMARY: SummaryPayload = {
  status: 'delta',
  previous_visit_at: '2026-07-09T14:30:00+00:00',
  baseline_snapshot_at: '2026-07-09T06:00:00+00:00',
  headline:
    'Since your last login: +1.5% high-opportunity, +2,250 refi candidates, +4,120 offers available.',
  phrasing_source: 'deterministic',
  phrasing_fallback_reason: 'genie_not_configured',
  highlights: [
    {
      measure: 'high_opportunity',
      label: 'high-opportunity',
      display: '+1.5%',
      value_token: '+1.5%',
      current: 88210,
      baseline: 86900,
      delta: 1310,
      delta_pct: 1.5,
    },
    {
      measure: 'refi_economics_screen',
      label: 'refi candidates',
      display: '+2,250',
      value_token: '+2,250',
      current: 261400,
      baseline: 259150,
      delta: 2250,
      delta_pct: 0.9,
    },
    {
      measure: 'offers_available',
      label: 'offers available',
      display: '+4,120',
      value_token: '+4,120',
      current: 402330,
      baseline: 398210,
      delta: 4120,
      delta_pct: 1.0,
    },
  ],
  current: {},
  baseline: {},
  deltas: {},
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

async function summaryNumbers(page: Page): Promise<string[]> {
  const nums = page.locator('.login-summary__num');
  return nums.allTextContents();
}

test.describe('S4 home summary — pinned response cross-check', () => {
  test.skip(LIVE, 'pinned mode; the live cross-check below covers E2E_LIVE=1');
  // First page load after a cold vite boot can take >30s to transform the
  // module graph; match module0.spec.ts's budget.
  test.describe.configure({ timeout: 90_000 });

  test.beforeEach(async ({ page }) => {
    await page.route(apiPattern('home/summary$'), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(PINNED_SUMMARY),
      });
    });
    // The summary renders after the KPI preview resolves (it shares the
    // hero column). Pin the preview + health too so the spec is hermetic
    // and never waits on a cold serverless warehouse.
    await page.route(apiPattern('portfolio/preview$'), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          marketable_population: 5240100,
          high_intent_leads: 261400,
          top_tier_opportunities: 88210,
          offers_recommended: 310450,
          offers_available: 402330,
          avg_score: 61,
          trends: {},
          trend_status: 'live',
          trend_note: null,
          data_refreshed_at: null,
          approved_count: 2,
          in_outreach_count: 1,
          day_zero: false,
        }),
      });
    });
    await page.route(apiPattern('health$'), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          mode: 'live',
          dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
          circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
        }),
      });
    });
  });

  test('renders exactly the API display tokens as evidence numbers', async ({ page }) => {
    await page.goto('/');
    const summary = page.locator('.login-summary');
    await expect(summary).toBeVisible({ timeout: 60_000 });
    await expect(summary).toContainText('Since your last login');

    await expect
      .poll(() => summaryNumbers(page), { timeout: 10_000 })
      .toEqual(PINNED_SUMMARY.highlights.map((h) => h.display));
  });

  test('every number opens the EvidenceDrawer citing snapshot + metric view', async ({ page }) => {
    await page.goto('/');
    const summary = page.locator('.login-summary');
    await expect(summary).toBeVisible({ timeout: 60_000 });

    for (let i = 0; i < PINNED_SUMMARY.highlights.length; i += 1) {
      await summary.locator('.login-summary__num').nth(i).click();
      const drawer = page.locator('.drawer');
      await expect(drawer).toBeVisible();
      await expect(drawer).toContainText('mip_app.kpi_snapshots');
      await expect(drawer).toContainText('mip.semantics.portfolio_headline_metric_view');
      await expect(drawer).toContainText(
        PINNED_SUMMARY.highlights[i].current.toLocaleString('en-US'),
      );
      await page.keyboard.press('Escape');
      await expect(drawer).toBeHidden();
    }
  });
});

test.describe('S4 home summary — live API cross-check', () => {
  test.skip(!LIVE, 'Set E2E_LIVE=1 in the nightly workflow to run real-UC e2e.');
  test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });
  test.describe.configure({ timeout: 120_000 });

  test('deployed UI renders exactly the numbers the API returned', async ({ page, request }) => {
    const res = await request.get(`${API_URL}/api/v1/home/summary`, {
      headers: AUTH_HEADERS,
    });
    expect(res.ok(), `GET /api/v1/home/summary -> ${res.status()}`).toBeTruthy();
    const summaryPayload = (await res.json()) as SummaryPayload;
    expect(['delta', 'first_visit', 'no_baseline']).toContain(summaryPayload.status);
    expect(summaryPayload.highlights.length).toBeGreaterThan(0);

    await page.goto('/', { waitUntil: 'domcontentloaded', timeout: 60_000 });
    const summary = page.locator('.login-summary');
    await expect(summary).toBeVisible({ timeout: 60_000 });

    // Cross-check: the UI must render the API's display tokens verbatim,
    // in order. (The visit recorded by this page load anchors NEXT session's
    // delta, not this one, so the two reads agree within the cache window.)
    await expect
      .poll(() => summaryNumbers(page), { timeout: 15_000 })
      .toEqual(summaryPayload.highlights.map((h) => h.display));

    if (summaryPayload.status === 'delta') {
      await expect(summary).toContainText('Since your last login');
      // Evidence: a delta number cites the real snapshot row + metric view.
      await summary.locator('.login-summary__num').first().click();
      const drawer = page.locator('.drawer');
      await expect(drawer).toBeVisible();
      await expect(drawer).toContainText('mip_app.kpi_snapshots');
      await expect(drawer).toContainText('mip.semantics.portfolio_headline_metric_view');
    } else {
      // Welcome states stay honest: no delta language, live numbers only.
      await expect(summary).not.toContainText('Since your last login:');
      expect(summaryPayload.deltas).toBeNull();
    }
  });
});
