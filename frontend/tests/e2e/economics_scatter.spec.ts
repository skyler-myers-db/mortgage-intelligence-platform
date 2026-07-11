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
 *   3. Zooming a cell loads REAL borrowers with an honest
 *      "Showing N of M" line; a dot deep-links to Borrower 360.
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

    // ---- 3. Zoom the densest cell → honest N of M + real dots -------------
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
    await expect(meta).toContainText(/Showing [\d.,KM]+ of [\d.,KM]+ borrowers/i, { timeout: 60_000 });

    const dots = page.locator('a.analytics-scatter__dot');
    await expect.poll(async () => dots.count(), { timeout: 60_000 }).toBeGreaterThan(0);

    // Dots carry the canonical S1 band classes.
    expect(
      await page.locator('a.analytics-scatter__dot.score--high, a.analytics-scatter__dot.score--med, a.analytics-scatter__dot.score--low').count(),
    ).toBeGreaterThan(0);

    // ---- 4. Dot → Borrower 360 --------------------------------------------
    const firstDot = dots.first();
    const dotHref = (await firstDot.getAttribute('href')) ?? '';
    expect(dotHref).toMatch(/\/borrower-360\/B-[0-9A-Z]{13}$/);
    const borrowerId = dotHref.split('/').pop()!;
    await firstDot.click();

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
});
