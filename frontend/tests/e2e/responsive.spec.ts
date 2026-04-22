/**
 * Module 0 — responsive + density/theme round-trip coverage.
 *
 * The AppShell is explicitly designed for 1440×900 and above: the Rail is
 * a fixed 72px wide column in a `grid-template-areas: "rail topbar" /
 * "rail main"` layout (see design-system/components.css L9-L19). There
 * is NO mobile-breakpoint that collapses the rail — the prototype is
 * desktop-only. The real responsive breakpoints in components.css are:
 *
 *   - `.seg-grid`    6col → 3col at max-width 1400px
 *   - `.kpi-row`     4col → 2col at max-width 1200px
 *   - `.layoutA-grid` 2col → 1col at max-width 1200px
 *
 * So the "mobile" test in this file asserts the 1200px breakpoint
 * actually fires (which is what the CSS declares), NOT that the rail
 * collapses (it doesn't, by design). Any future mobile pass would need
 * CSS work first; this test is the canary that catches the existing
 * breakpoints regressing.
 *
 * Theme / density toggles ARE first-class AppContext state and are
 * covered here end-to-end.
 *
 * Gated on `E2E_LIVE=1` to match real_data.spec.ts — backend boot
 * requires live Databricks creds.
 */
import { test, expect } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run responsive/density coverage.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
test.use({ baseURL: APP_URL });

test.describe('Module 0 — responsive + theme/density', () => {
  test('theme toggle round-trip: dark <-> light + background color token changes', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');
    const body = page.locator('body');

    // Capture initial theme + computed bg. data-theme="dark" is the
    // product default (design_files/index.html), but respect whatever
    // the app reports to avoid flake from persisted localStorage.
    const initialTheme = await html.getAttribute('data-theme');
    const initialBg = await body.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );

    // Use the Topbar theme toggle — the "Toggle theme" aria-label
    // scopes the click unambiguously (the Admin Config page also has
    // Dark/Light buttons, but that's a different route).
    await page.getByRole('banner').getByRole('button', { name: 'Toggle theme' }).click();

    const nextTheme = initialTheme === 'dark' ? 'light' : 'dark';
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(nextTheme);

    // Assert the background computed color actually changed. This is the
    // "token really flipped" proof — dark and light both set `--bg-1`
    // which body reads, so the visible RGB triple should differ.
    await expect
      .poll(async () => body.evaluate((el) => getComputedStyle(el).backgroundColor), {
        timeout: 3_000,
      })
      .not.toBe(initialBg);

    // Flip back so test isolation holds.
    await page.getByRole('banner').getByRole('button', { name: 'Toggle theme' }).click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(initialTheme);
  });

  test('density round-trip via Console: comfortable <-> compact', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');

    // Open the Console from the Topbar (rightmost icon, "Toggle console").
    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
    const consoleDialog = page.getByRole('dialog', { name: 'Console' });
    await expect(consoleDialog).toBeVisible();

    const initialDensity = await html.getAttribute('data-density');
    const nextDensityLabel = initialDensity === 'compact' ? 'Comfortable' : 'Compact';
    await consoleDialog.getByRole('button', { name: nextDensityLabel }).click();

    await expect
      .poll(() => html.getAttribute('data-density'), { timeout: 3_000 })
      .toBe(nextDensityLabel.toLowerCase());

    // Flip back to leave the app in its starting state.
    const origLabel = initialDensity === 'compact' ? 'Compact' : 'Comfortable';
    await consoleDialog.getByRole('button', { name: origLabel }).click();
    await expect
      .poll(() => html.getAttribute('data-density'), { timeout: 3_000 })
      .toBe(initialDensity);
  });

  test('narrow viewport (1150px): kpi-row and layoutA-grid collapse to single column', async ({ page }) => {
    // 1150 is inside the 1200px breakpoint (components.css L818) but wide
    // enough that the fixed 72px Rail + main content still fit — the
    // prototype is desktop-only by design; see file header.
    await page.setViewportSize({ width: 1150, height: 900 });
    await page.goto('/');

    // Wait for hero so the shell is fully hydrated.
    await expect(
      page.getByRole('heading', { name: /Who should we contact/i }),
    ).toBeVisible({ timeout: 10_000 });

    // .kpi-row should render as 2-col (max-width: 1200px breakpoint).
    // We read the computed grid-template-columns: the 2-col rule yields
    // a string with exactly two track sizes.
    const kpiCols = await page.locator('.kpi-row').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns.split(' ').filter((x) => x.trim().length > 0).length;
    });
    expect(kpiCols, 'expected .kpi-row to render 2 columns at 1150px').toBe(2);

    // .layoutA-grid (below the KPIs) collapses to 1 column.
    const layoutCols = await page.locator('.layoutA-grid').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns.split(' ').filter((x) => x.trim().length > 0).length;
    });
    expect(layoutCols, 'expected .layoutA-grid to render 1 column at 1150px').toBe(1);

    // Rail is NOT responsive by design — assert it stays at 72px so we
    // catch any regression that tries to hide it on narrower widths.
    const railWidth = await page.locator('.rail').first().evaluate((el) => el.getBoundingClientRect().width);
    expect(railWidth, 'Rail must stay 72px wide — prototype is desktop-only').toBeGreaterThanOrEqual(60);
    expect(railWidth).toBeLessThanOrEqual(90);
  });

  test('segment-intelligence: seg-grid collapses from 6 to 3 cols at 1400px', async ({ page }) => {
    // The segment grid has its own breakpoint at 1400px
    // (components.css L809). Test at 1300 to stay safely inside it.
    await page.setViewportSize({ width: 1300, height: 900 });
    await page.goto('/segment-intelligence');

    // Wait for segment cards to render from /api/segments.
    await expect(page.locator('.seg-grid').first()).toBeVisible({ timeout: 30_000 });

    const segCols = await page.locator('.seg-grid').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns.split(' ').filter((x) => x.trim().length > 0).length;
    });
    expect(segCols, 'expected .seg-grid to render 3 columns at 1300px').toBe(3);
  });
});
