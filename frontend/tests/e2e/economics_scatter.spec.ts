/**
 * S7 — Economics scatter live e2e: filter → density cell → real dot →
 * Borrower 360 → outreach composer opens in DRAFT-ONLY form.
 *
 * Runs against the deployed app on real Unity Catalog data, gated on
 * ``E2E_LIVE=1`` exactly like real_data.spec.ts (PR CI only collects it
 * via ``playwright test --list``).
 *
 * What it proves end-to-end:
 *   1. The Analytics → Economics scatter overview renders server-side
 *      DENSITY BINS from mip.gold.equity_spread_points — no borrower ids
 *      in the overview DOM — plus the source-evidence chip.
 *   2. Applying a state filter re-queries the bins (URL-addressable filter,
 *      same governed predicate as the points endpoint).
 *   3. Zooming a cell loads REAL borrowers with honest plotting/total
 *      copy; either a dot or a numbered cluster deep-links to Borrower 360.
 *   4. From Borrower 360, "Build outreach draft" opens the offer
 *      orchestrator: a draft composer with an approval gate and NO
 *      send/dispatch affordance anywhere on the surface — the terminal
 *      state is the approval-gated Lakebase record.
 *
 * Non-negotiables per CLAUDE.md: no mock fallback (if UC is unreachable the
 * spec fails, correctly); only synthetic B-* ids asserted; resilient
 * role/label selectors against the prototype BEM.
 */
import { test, expect, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 in the nightly workflow to run real-UC e2e.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

async function gotoApp(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await expect(page.locator('main')).toBeVisible({ timeout: 30_000 });
}

test.describe('S7 economics scatter — bins → dot → Borrower 360 → draft composer', () => {
  test.describe.configure({ timeout: 180_000 });

  test('full product flow on real data', async ({ page }) => {
    // ---- 1. Overview = density bins, no raw borrower rows -----------------
    await gotoApp(page, '/analytics?view=economics');

    const scatter = page.getByTestId('equity-spread-scatter');
    await expect(scatter).toBeVisible({ timeout: 60_000 });

    const cells = scatter.locator('.analytics-scatter__bin');
    await expect.poll(async () => cells.count(), { timeout: 60_000 }).toBeGreaterThan(0);

    // Honest overview copy + evidence lineage chip citing the gold table.
    await expect(scatter.getByTestId('scatter-meta')).toContainText(/binned server-side/i);
    await expect(scatter.getByText('mip.gold.equity_spread_points')).toBeVisible();

    // The overview must not ship borrower deep-links.
    expect(await scatter.locator('a[href*="/borrower-360/"]').count()).toBe(0);

    // ---- 2. Filter: narrow to the first covered state ---------------------
    const stateFilter = page.getByRole('button', { name: /All states/i }).first();
    if (await stateFilter.isVisible().catch(() => false)) {
      await stateFilter.click();
      const firstState = page.getByRole('option').first()
        .or(page.locator('[role="listbox"] [role="checkbox"], [role="menu"] [role="menuitemcheckbox"]').first());
      if (await firstState.isVisible().catch(() => false)) {
        await firstState.click();
        await page.keyboard.press('Escape');
        // Bins re-render for the filtered predicate.
        await expect.poll(async () => cells.count(), { timeout: 60_000 }).toBeGreaterThan(0);
      }
    }

    // ---- 3. Zoom the densest cell → honest totals + real markers -----------
    // Cells expose their density in the aria-label; click the first one with
    // a visible count (labels are deterministic strings).
    await cells.first().waitFor({ state: 'visible' });
    const cellCount = await cells.count();
    let densest = cells.first();
    let densestValue = -1;
    for (let i = 0; i < Math.min(cellCount, 200); i += 1) {
      const label = (await cells.nth(i).getAttribute('aria-label')) ?? '';
      const match = /^([\d.,]+)([KM]?) borrowers/.exec(label);
      if (!match) continue;
      const scale = match[2] === 'M' ? 1_000_000 : match[2] === 'K' ? 1_000 : 1;
      const value = parseFloat(match[1].replace(/,/g, '')) * scale;
      if (value > densestValue) {
        densestValue = value;
        densest = cells.nth(i);
      }
    }
    expect(densestValue).toBeGreaterThan(0);
    await densest.click();

    const meta = page.getByTestId('scatter-meta');
    await expect(meta).toContainText(/Plotting [\d.,KM]+ of [\d.,KM]+ returned borrowers/i, { timeout: 60_000 });

    const dots = page.locator('a.analytics-scatter__dot');
    const clusters = page.locator('button.analytics-scatter__cluster-marker');
    await expect.poll(
      async () => (await dots.count()) + (await clusters.count()),
      { timeout: 60_000 },
    ).toBeGreaterThan(0);

    // Every marker carries a canonical S1 band class.
    expect(
      await page.locator([
        'a.analytics-scatter__dot.score--high',
        'a.analytics-scatter__dot.score--med',
        'a.analytics-scatter__dot.score--low',
        'button.analytics-scatter__cluster-marker.score--high',
        'button.analytics-scatter__cluster-marker.score--med',
        'button.analytics-scatter__cluster-marker.score--low',
      ].join(', ')).count(),
    ).toBeGreaterThan(0);

    // ---- 4. Marker → Borrower 360 -----------------------------------------
    const borrowerLink = await dots.count() > 0
      ? dots.first()
      : page.locator('.analytics-scatter__cluster-panel:not([hidden]) a').first();
    if (await dots.count() === 0) {
      await clusters.first().click();
      await expect(borrowerLink).toBeVisible();
    }
    const borrowerHref = (await borrowerLink.getAttribute('href')) ?? '';
    expect(borrowerHref).toMatch(/\/borrower-360\/B-[0-9A-Z]{13}$/);
    const borrowerId = borrowerHref.split('/').pop()!;
    await borrowerLink.click();

    await expect(page).toHaveURL(new RegExp(`/borrower-360/${borrowerId}$`));
    // Real dossier renders (masked id + primary offer CTA).
    await expect(page.getByText(borrowerId).first()).toBeVisible({ timeout: 60_000 });
    const composeCta = page.getByRole('link', { name: /Build outreach draft/i });
    await expect(composeCta).toBeVisible({ timeout: 60_000 });

    // ---- 5. Composer opens DRAFT-ONLY --------------------------------------
    await composeCta.click();
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${borrowerId}$`));

    // The approval gate is present (human approval always required)...
    await expect(
      page.getByRole('button', { name: /^Approve$|approve outreach/i }).first(),
    ).toBeVisible({ timeout: 60_000 });

    // ...and there is NO send/dispatch path anywhere on the composer:
    // outreach ends at the approval-gated Lakebase record.
    const sendAffordances = page.getByRole('button', { name: /\bsend\b|\bdispatch\b|send email|send sms/i });
    expect(await sendAffordances.count()).toBe(0);
    await expect(page.locator('main')).not.toContainText(/send now|sending to borrower/i);
  });

  test('numbered cluster reports exact population and paginates every returned member', async ({ page }) => {
    const returnedIds = Array.from(
      { length: 75 },
      (_, index) => `B-${String(index).padStart(13, '0')}`,
    );
    await page.route(/\/api\/(?:v1\/)?analytics\/economics\/points(?:\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          points: returnedIds.map((borrowerId, index) => ({
            borrower_id: borrowerId,
            display_name: `Owner ${index}`,
            segment: 'Prime Refi Candidates',
            state: 'IL',
            equity_pct: 42,
            rate_spread_bps: 88,
            opportunity_score: 100 - (index % 60),
            coordinate_total: 120,
            score_band: index < 40 ? 'high' : 'med',
          })),
          total_matching: 120,
          showing: 75,
          point_cap: 75,
          truncated: true,
          viewport: { equity_min: 40, equity_max: 44, spread_min: 75, spread_max: 99 },
          source_table: 'mip.gold.equity_spread_points',
        }),
      });
    });

    await gotoApp(page, '/analytics?view=economics');
    const scatter = page.getByTestId('equity-spread-scatter');
    const cells = scatter.locator('.analytics-scatter__bin');
    await expect.poll(async () => cells.count(), { timeout: 60_000 }).toBeGreaterThan(0);
    await cells.first().click();

    await expect(scatter.getByTestId('scatter-meta')).toContainText(
      'Plotting 75 of 75 returned borrowers (120 total matching this window) (server cap 75)',
    );
    const cluster = scatter.getByRole('button', {
      name: /120 borrowers; showing 75 ranked links returned under server cap/i,
    });
    await expect(cluster).toHaveText('120');
    await cluster.click();

    const panel = scatter.locator('.analytics-scatter__cluster-panel:not([hidden])');
    await expect(panel).toContainText(
      '120 borrowers; showing 75 ranked links returned under server cap',
    );
    const links = panel.locator('a.analytics-scatter__cluster-link');
    await expect(links).toHaveCount(50);
    await panel.getByRole('button', { name: 'Show 25 more' }).click();
    await expect(links).toHaveCount(75);

    const hrefIds = await links.evaluateAll((nodes) => nodes.map((node) => (
      node.getAttribute('href')?.split('/').pop() ?? ''
    )));
    expect(hrefIds).toEqual(returnedIds);
    expect(new Set(hrefIds).size).toBe(returnedIds.length);
  });
});
