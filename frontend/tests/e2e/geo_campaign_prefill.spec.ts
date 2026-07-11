import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * S9 — geo drill-down assigned-vs-unattended overlay + campaign-from-geo
 * prefill, on pinned fixture responses (same offline-mock pattern as
 * module0.spec.ts; real_data.spec.ts owns live validation).
 *
 * Flow under test:
 *   home map → drill Illinois → toggle "Unattended leads" → drill Cook
 *   County → ZIP grid shows per-tile unattended counts → "Start campaign"
 *   → Portfolio Builder shows the typed prefilled draft context.
 */

const API_PREFIX = /\/api\/(?:v1\/)?/;
const apiPattern = (path: string) => new RegExp(`${API_PREFIX.source}${path}`);

// Same convention as the live specs: MIP_APP_URL overrides the app origin so
// the spec can run against an explicitly-started dev server (multi-worktree
// machines share :5173; reuseExistingServer would otherwise test a sibling
// checkout's code).
const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';

const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function mockShell(page: Page) {
  await page.route(apiPattern('health$'), (route) =>
    json(route, {
      status: 'ok',
      mode: 'live',
      dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
      circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
    }),
  );
  await page.route(apiPattern('workspace$'), (route) =>
    json(route, { saved_leads: [], saved_drafts: [] }),
  );
  await page.route(apiPattern('config/options$'), (route) =>
    json(route, {
      lender_name: 'Summit Mortgage',
      rum_enabled: false,
      geographies: ['All states', 'Illinois', 'Texas'],
      geographies_status: 'live',
      occupancy: ['All', 'Owner-occupied'],
      lien_status: ['Any', 'Open 1st lien'],
      lender_relationships: ['All', 'Competitor customer'],
      products: ['All products', 'Refi', 'HELOC'],
      equity_thresholds: ['Any', '35%+'],
      target_lender_refs: ['All'],
      target_lender_refs_status: 'live',
    }),
  );
  await page.route(apiPattern('config/footprint$'), (route) =>
    json(route, {
      states: [
        { state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true },
        { state_code: 'TX', state_name: 'Texas', display_order: 2, is_default_state: false },
      ],
      using_fallback: false,
      geography_scope: {
        state_count: 2,
        county_count: 2,
        zip_count: 2,
        scope_label: 'Cotality data coverage: 2 counties across 2 states',
        counties: [
          { state: 'IL', fips_5: '17031', county_name: 'Cook County', addressable_borrowers: 620 },
          { state: 'TX', fips_5: '48201', county_name: 'Harris County', addressable_borrowers: 540 },
        ],
      },
    }),
  );
  await page.route(apiPattern('growth-agent(?:/monitors)?$'), (route) =>
    json(route, route.request().url().endsWith('/monitors') ? [] : { workflows: [], monitors: [] }),
  );
}

async function mockGeo(page: Page) {
  await page.route(apiPattern('geo/state-rollups'), (route) =>
    json(route, {
      rollups: [
        { state: 'IL', addressable: 1860, in_the_money: 720, top_tier_opportunities: 420, avg_score: 84, top_segment_code: 'itm' },
        { state: 'TX', addressable: 750, in_the_money: 340, top_tier_opportunities: 220, avg_score: 82, top_segment_code: 'equity' },
      ],
      snapshot_date: '2026-07-10',
    }),
  );
  await page.route(apiPattern('geo/county-rollups'), (route) =>
    json(route, {
      state: 'IL',
      rollups: [
        { fips_5: '17031', state: 'IL', county_name: 'Cook', addressable_borrowers: 620, in_the_money_borrowers: 310, high_opportunity_borrowers: 260, avg_opportunity_score: 86, top_segment_code: 'itm' },
      ],
      snapshot_date: '2026-07-10',
      scope_note: 'Cotality data coverage for IL: 1 county returned',
    }),
  );
  await page.route(apiPattern('geo/zip-rollups'), (route) =>
    json(route, {
      fips_5: '17031',
      rollups: [
        { zip: '60611', state: 'IL', county_fips_5: '17031', addressable_borrowers: 94, avg_opportunity_score: 94, top_segment_code: 'itm', sample_borrower_id: 'B-0000000000001' },
        { zip: '60647', state: 'IL', county_fips_5: '17031', addressable_borrowers: 72, avg_opportunity_score: 82, top_segment_code: 'equity', sample_borrower_id: 'B-0000000000002' },
      ],
      snapshot_date: '2026-07-10',
    }),
  );
  // Assigned-vs-unattended overlay: one handler, level-keyed like the API.
  await page.route(apiPattern('geo/assignment-overlay'), (route) => {
    const url = new URL(route.request().url());
    const level = url.searchParams.get('level') ?? 'state';
    if (level === 'state') {
      return json(route, {
        level: 'state',
        state: null,
        county_fips: null,
        units: [
          { unit_id: 'IL', lead_count: 1240, assigned_count: 140, unattended_count: 1100, covering_officer_count: 1, covering_officers: ['Summit LO 01'] },
          { unit_id: 'TX', lead_count: 505, assigned_count: 0, unattended_count: 505, covering_officer_count: 1, covering_officers: ['Summit LO 03'] },
        ],
        total_leads: 1745,
        total_assigned: 140,
        total_unattended: 1605,
        lead_definition: 'Marketing-eligible borrowers in the live lead queue (mip.gold.borrower_360, marketing_eligible = TRUE)',
      });
    }
    if (level === 'county') {
      return json(route, {
        level: 'county',
        state: url.searchParams.get('state') ?? 'IL',
        county_fips: null,
        units: [
          { unit_id: '17031', lead_count: 410, assigned_count: 96, unattended_count: 314, covering_officer_count: 1, covering_officers: ['Summit LO 01'] },
        ],
        total_leads: 410,
        total_assigned: 96,
        total_unattended: 314,
        lead_definition: 'Marketing-eligible borrowers in the live lead queue (mip.gold.borrower_360, marketing_eligible = TRUE)',
      });
    }
    return json(route, {
      level: 'zip',
      state: 'IL',
      county_fips: url.searchParams.get('county_fips') ?? '17031',
      units: [
        { unit_id: '60611', lead_count: 64, assigned_count: 21, unattended_count: 43, covering_officer_count: 1, covering_officers: ['Summit LO 01'] },
        { unit_id: '60647', lead_count: 48, assigned_count: 9, unattended_count: 39, covering_officer_count: 1, covering_officers: ['Summit LO 01'] },
      ],
      total_leads: 112,
      total_assigned: 30,
      total_unattended: 82,
      lead_definition: 'Marketing-eligible borrowers in the live lead queue (mip.gold.borrower_360, marketing_eligible = TRUE)',
    });
  });
}

async function mockPortfolioBuilder(page: Page) {
  await page.route(apiPattern('campaigns$'), (route) => json(route, { campaigns: [] }));
  await page.route(apiPattern('portfolio/preview$'), (route) =>
    json(route, {
      marketable_population: 620,
      high_intent_leads: 260,
      top_tier_opportunities: 120,
      offers_recommended: 84,
      avg_score: 82,
      data_refreshed_at: '2026-07-10T06:00:00Z',
      day_zero: false,
      approved_count: 0,
      in_outreach_count: 0,
    }),
  );
}

test.describe('S9 — geo overlay + campaign-from-geo prefill', () => {
  test.describe.configure({ timeout: 90_000 });
  test.skip(
    process.env.E2E_LIVE === '1',
    'geo_campaign_prefill.spec.ts pins offline fixture data; live overlay math is covered by tests/integration/test_geo_assignment_overlay_live.py',
  );

  test.beforeEach(async ({ page }) => {
    await mockShell(page);
    await mockGeo(page);
    await mockPortfolioBuilder(page);
  });

  test('drill to ZIP grid, toggle unattended overlay, start prefilled campaign draft', async ({ page }) => {
    await page.goto(APP_URL + '/');

    // --- State level: map up, drill into Illinois. ---
    const illinois = page.locator('[aria-label="Illinois"]').first();
    await expect(illinois).toBeVisible({ timeout: 45_000 });
    await illinois.click();

    // --- County level: the drilled state is the selected unit, so the
    // campaign affordance is already available. Toggle the overlay. ---
    await expect(page.getByRole('button', { name: 'Start campaign' })).toBeVisible();
    await page.getByRole('button', { name: 'Unattended leads' }).click();
    await expect(page.getByText('Unattended leads in selection')).toBeVisible();
    await expect(page.locator('.map-legend')).toContainText('314');
    await expect(page.locator('.map-legend')).toContainText('410 leads · 96 assigned');
    // Overlay evidence affordance present (never removed).
    await expect(page.locator('.map-legend .evidence-chip')).toBeVisible();

    // --- ZIP level: drill Cook County; tiles carry unattended counts. ---
    await page.locator('[aria-label="Cook County"]').click();
    await expect(page.locator('.zip-tiles')).toBeVisible({ timeout: 15_000 });
    const firstTile = page.locator('.zip-tile').first();
    await expect(firstTile).toContainText('60611');
    await expect(firstTile).toContainText('43 unattended');
    await expect(page.getByText('Unattended leads in selection')).toBeVisible();
    await expect(page.locator('.map-legend')).toContainText('82');

    // --- Start campaign: navigates to the Portfolio Builder with the
    // typed prefill contract in the URL. ---
    await page.getByRole('button', { name: 'Start campaign' }).click();
    await expect(page).toHaveURL(/\/portfolio-builder\?/);
    const url = new URL(page.url());
    expect(url.searchParams.get('prefill_source')).toBe('geo-drilldown');
    expect(url.searchParams.get('prefill_level')).toBe('county');
    expect(url.searchParams.get('states')).toBe('IL');
    expect(url.searchParams.get('prefill_county_fips')).toBe('17031');
    expect(url.searchParams.get('prefill_lead_count')).toBe('112');
    expect(url.searchParams.get('prefill_unattended_count')).toBe('82');

    // --- Prefilled draft context is visible, with honest S10 copy. ---
    const banner = page.getByTestId('campaign-prefill-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Campaign draft from geography drill-down');
    await expect(banner).toContainText('State IL — applied');
    await expect(banner).toContainText('FIPS 17031');
    await expect(banner).toContainText('when the campaign builder (S10) ships');
    await expect(banner).toContainText('112 leads · 82 unattended at draft time');

    // The state predicate really applied: the GEO multi-select shows Illinois.
    await expect(page.getByRole('button', { name: /^GEO: Illinois/ })).toBeVisible();
  });

  test('overlay failure degrades honestly and keeps the borrower view', async ({ page }) => {
    await page.unroute(apiPattern('geo/assignment-overlay'));
    await page.route(apiPattern('geo/assignment-overlay'), (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'lakebase temporarily unavailable',
          retryable: true,
          dependency: 'lakebase',
          reason: 'breaker_open',
        }),
      }),
    );
    await page.goto(APP_URL + '/');
    await expect(page.locator('[aria-label="Illinois"]').first()).toBeVisible({ timeout: 45_000 });
    await page.getByRole('button', { name: 'Unattended leads' }).click();
    // Explicit degraded note; base borrower counts stay on the legend.
    await expect(page.locator('.map-legend')).toContainText('Coverage overlay unavailable', {
      timeout: 15_000,
    });
    await expect(page.getByText('Unattended leads in selection')).toBeVisible();
    // The map itself still renders regions (base coloring, not blanked).
    await expect(page.locator('[aria-label="Illinois"]').first()).toBeVisible();
  });
});
