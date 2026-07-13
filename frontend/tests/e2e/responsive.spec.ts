/**
 * Module 0 — responsive + density/theme round-trip coverage.
 *
 * Slice 14 responsive pass: container-query driven layout across 8
 * anchor widths from a 13-inch MacBook Air (1280×720) to a 4K monitor
 * (3840×2160). The AppShell uses viewport-scoped @media for the rail
 * width / topbar height / Console gutter (chrome is viewport-bound),
 * and the inner layout uses @container queries against `main` so the
 * Console-open state correctly re-flows without waiting on a viewport
 * resize.
 *
 * Breakpoint anchors (container-query):
 *   ≤ 640px    : 1-col stack (everything)
 *   641–960px  : 2-col KPI, 2-col seg, layoutA stacks
 *   961–1280px : 3-col KPI, 3-col seg, layoutA 2-col (1.2fr 1fr)
 *   1281–1680px: 4-col KPI, 6-col seg, layoutA 2-col (1.4fr 1fr)  ← design
 *   1681–2000px: 4-col KPI + extra gap, 6-col seg
 *   ≥ 2001px   : 4-col KPI + extra gap, 6-col seg, content capped at 1920px
 *
 * Theme / density toggles ARE first-class AppContext state and are
 * covered here end-to-end.
 *
 * Most tests are gated on `E2E_LIVE=1` to match real_data.spec.ts —
 * backend boot requires live Databricks credentials. The new responsive
 * anchor matrix stays gated the same way; the offline CI job still
 * collects these specs via `playwright test --list` for a syntax check.
 */
import { test, expect, type APIRequestContext, type Locator } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const ADMIN_BEARER = process.env.MIP_ADMIN_BEARER_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

async function fetchFirstLeadId(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=1`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
  const id = rows[0]?.borrower_id;
  expect(id, 'need a live borrower id for responsive deep-link coverage').toBeTruthy();
  return id!;
}

type Anchor = {
  width: number;
  height: number;
  label: string;
  kpiCols: number;     // expected .kpi-row track count on Home
  segCols: number;     // expected .seg-grid track count on Segment Intelligence
  layoutACols: number; // expected .layoutA-grid track count on Home
};

// Each anchor carries the expected column counts for its band. Console
// is closed by default so the main container width approximates the
// viewport width minus the 72–96px rail. Numbers match the container-
// query rules in components.css.
const ANCHORS: Anchor[] = [
  // viewport - 72px rail ≈ 1208 → medium band (961–1280)
  { width: 1280, height: 720, label: '1280-smallest-laptop', kpiCols: 3, segCols: 3, layoutACols: 2 },
  // viewport - 72px rail ≈ 1294 → design band (1281–1680)
  { width: 1366, height: 768, label: '1366-common-laptop', kpiCols: 4, segCols: 6, layoutACols: 2 },
  // 1440 - 72px ≈ 1368 → design band — prototype target
  { width: 1440, height: 900, label: '1440-design-target', kpiCols: 4, segCols: 6, layoutACols: 2 },
  // 1600 - 72px ≈ 1528 → design band
  { width: 1600, height: 900, label: '1600-mid-laptop', kpiCols: 4, segCols: 6, layoutACols: 2 },
  // 1920 - 80px (rail widens at 1920) ≈ 1840 → very-wide band (1681–2000)
  { width: 1920, height: 1080, label: '1920-fhd-projector', kpiCols: 4, segCols: 6, layoutACols: 2 },
  // 2560 - 96px (rail widens at 2560) ≈ 2464 → ultra-wide band (≥ 2001)
  { width: 2560, height: 1440, label: '2560-qhd', kpiCols: 4, segCols: 6, layoutACols: 2 },
  // 3440 - 96px ≈ 3344 → ultra-wide, content capped at 1920px
  { width: 3440, height: 1440, label: '3440-ultrawide', kpiCols: 4, segCols: 6, layoutACols: 2 },
  // 3840 - 96px ≈ 3744 → ultra-wide / 4K
  { width: 3840, height: 2160, label: '3840-4k', kpiCols: 4, segCols: 6, layoutACols: 2 },
];

function countTracks(columns: string): number {
  return columns.split(' ').filter((x) => x.trim().length > 0).length;
}

async function expectGridColumns(locator: Locator, expected: number, message: string) {
  await expect
    .poll(
      async () => {
        const columns = await locator.evaluate((el) => getComputedStyle(el).gridTemplateColumns);
        return countTracks(columns);
      },
      { message, timeout: 5_000 },
    )
    .toBe(expected);
}

test.describe('Module 0 — theme / density / narrow canaries', () => {
  test.skip(!LIVE, 'Set E2E_LIVE=1 to run responsive/density coverage.');
  test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

  test('theme toggle round-trip: dark <-> light + background color token changes', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');
    const body = page.locator('body');

    const initialTheme = await html.getAttribute('data-theme');
    const initialBg = await body.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );

    await page.getByRole('banner').getByRole('button', { name: 'Toggle theme' }).click();

    const nextTheme = initialTheme === 'dark' ? 'light' : 'dark';
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(nextTheme);

    await expect
      .poll(async () => body.evaluate((el) => getComputedStyle(el).backgroundColor), {
        timeout: 3_000,
      })
      .not.toBe(initialBg);

    await page.getByRole('banner').getByRole('button', { name: 'Toggle theme' }).click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(initialTheme);
  });

  test('density round-trip via Console: comfortable <-> compact', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');

    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
    const consoleDialog = page.getByRole('complementary', { name: 'Workspace console' });
    await expect(consoleDialog).toBeVisible();

    const initialDensity = await html.getAttribute('data-density');
    const nextDensityLabel = initialDensity === 'compact' ? 'Comfortable' : 'Compact';
    await consoleDialog.getByRole('button', { name: nextDensityLabel }).click();

    await expect
      .poll(() => html.getAttribute('data-density'), { timeout: 3_000 })
      .toBe(nextDensityLabel.toLowerCase());

    const origLabel = initialDensity === 'compact' ? 'Compact' : 'Comfortable';
    await consoleDialog.getByRole('button', { name: origLabel }).click();
    await expect
      .poll(() => html.getAttribute('data-density'), { timeout: 3_000 })
      .toBe(initialDensity);
  });

  test('narrow viewport (1150px): rail stays fixed; kpi-row and layoutA-grid collapse', async ({ page }) => {
    await page.setViewportSize({ width: 1150, height: 900 });
    await page.goto('/');

    await expect(
      page.getByRole('heading', { name: /Who should we contact/i }),
    ).toBeVisible({ timeout: 10_000 });

    // 1150 viewport - 72px rail ≈ 1078 → medium band → 3-col KPI.
    const kpiCols = await page.locator('.kpi-row').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns
        .split(' ')
        .filter((x) => x.trim().length > 0).length;
    });
    expect(kpiCols, 'expected .kpi-row to render 3 columns at 1150px').toBe(3);

    // .layoutA-grid (below the KPIs) stays 2-col in the medium band.
    const layoutCols = await page.locator('.layoutA-grid').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns
        .split(' ')
        .filter((x) => x.trim().length > 0).length;
    });
    expect(layoutCols, 'expected .layoutA-grid to render 2 columns at 1150px').toBe(2);

    // Rail is NOT responsive by design (until < 1024 viewport) — assert it stays
    // at 72px so we catch any regression that tries to hide it.
    const railWidth = await page.locator('.rail').first().evaluate((el) => el.getBoundingClientRect().width);
    expect(railWidth, 'Rail must stay ~72px wide — prototype is desktop-only').toBeGreaterThanOrEqual(60);
    expect(railWidth).toBeLessThanOrEqual(90);
  });

  test('segment-intelligence: seg-grid gives 6 cols at the design target (1440)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/segment-intelligence');

    await expect(page.locator('.seg-grid').first()).toBeVisible({ timeout: 30_000 });

    const segCols = await page.locator('.seg-grid').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns
        .split(' ')
        .filter((x) => x.trim().length > 0).length;
    });
    expect(segCols, 'expected .seg-grid to render 6 columns at 1440px').toBe(6);
  });

  test('segment-intelligence phone viewport stacks segment cards without document clipping', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/segment-intelligence');

    const grid = page.locator('.seg-grid').first();
    await expect(grid).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Prime Refi Candidates', { exact: true }).first()).toBeVisible();

    const segCols = await grid.evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns
        .split(' ')
        .filter((x) => x.trim().length > 0).length;
    });
    expect(segCols, 'phone segment cards should stack to one column').toBe(1);

    const overflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth - document.documentElement.clientWidth;
    });
    expect(overflow, 'segment intelligence should not create document-level horizontal scroll').toBeLessThanOrEqual(2);
  });

  test('analytics scatter stays legible and keyboard-navigable at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/analytics');

    await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const scatterHeading = page.getByRole('heading', { name: 'Equity vs Rate Spread' });
    await expect(scatterHeading).toBeVisible({ timeout: 60_000 });
    await scatterHeading.scrollIntoViewIfNeeded();
    await expect(page.getByText('Opportunity score 75–100')).toBeVisible();
    await expect(page.getByText('Opportunity score 50–74')).toBeVisible();
    await expect(page.getByText('Opportunity score 0–49')).toBeVisible();

    const markers = page.locator('[data-scatter-marker]');
    await expect.poll(() => markers.count(), { timeout: 60_000 }).toBeGreaterThan(1);
    await markers.first().focus();
    await page.keyboard.press('ArrowRight');
    await expect(markers.nth(1)).toBeFocused();

    const metrics = await page.evaluate(() => {
      const plot = document.querySelector('.analytics-scatter') as HTMLElement | null;
      const legend = document.querySelector('.analytics-scatter-legend') as HTMLElement | null;
      if (!plot || !legend) return null;
      const plotRect = plot.getBoundingClientRect();
      const legendRect = legend.getBoundingClientRect();
      return {
        documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        plotLeft: plotRect.left,
        plotRight: plotRect.right,
        legendLeft: legendRect.left,
        legendRight: legendRect.right,
        viewportWidth: document.documentElement.clientWidth,
      };
    });
    expect(metrics, 'scatter and legend should render').toBeTruthy();
    expect(metrics!.documentOverflow, 'analytics should not create document-level horizontal scroll').toBeLessThanOrEqual(2);
    expect(metrics!.plotLeft).toBeGreaterThanOrEqual(0);
    expect(metrics!.plotRight).toBeLessThanOrEqual(metrics!.viewportWidth + 2);
    expect(metrics!.legendLeft).toBeGreaterThanOrEqual(0);
    expect(metrics!.legendRight).toBeLessThanOrEqual(metrics!.viewportWidth + 2);
  });

  test('lead queue phone viewport keeps approval column reachable inside table scroller', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/lead-queue');

    const wrapper = page.locator('.tbl-wrap').first();
    await expect(wrapper).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.lead-table__approval-header')).toBeVisible();

    const metrics = await page.evaluate(() => {
      const wrap = document.querySelector('.tbl-wrap') as HTMLElement | null;
      const header = document.querySelector('.lead-table__approval-header') as HTMLElement | null;
      if (!wrap || !header) return null;
      wrap.scrollLeft = wrap.scrollWidth;
      const wrapRect = wrap.getBoundingClientRect();
      const headerRect = header.getBoundingClientRect();
      return {
        documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        wrapLeft: wrapRect.left,
        wrapRight: wrapRect.right,
        headerLeft: headerRect.left,
        headerRight: headerRect.right,
      };
    });

    expect(metrics, 'lead queue table and approval header should render').toBeTruthy();
    expect(metrics!.documentOverflow, 'lead queue should not create document-level horizontal scroll').toBeLessThanOrEqual(2);
    expect(metrics!.headerLeft, 'approval header should be reachable at table scroll end').toBeGreaterThanOrEqual(metrics!.wrapLeft - 2);
    expect(metrics!.headerRight, 'approval header should stay inside the table viewport at scroll end').toBeLessThanOrEqual(metrics!.wrapRight + 2);
  });

  test('admin-config phone viewport stacks admin operations without clipping', async ({ page }) => {
    test.skip(!ADMIN_BEARER, 'Requires MIP_ADMIN_BEARER_TOKEN for admin-only phone clipping canary.');

    await page.setExtraHTTPHeaders({ Authorization: `Bearer ${ADMIN_BEARER}` });
    await page.setViewportSize({ width: 390, height: 844 });
    const operationsResponse = page.waitForResponse((response) => {
      return response.url().includes('/api/v1/admin/operations') && response.status() === 200;
    }, { timeout: 120_000 });
    await page.goto('/admin-config');

    await expect(page.getByRole('heading', { name: /Rules, data sources, and audit/i })).toBeVisible({
      timeout: 30_000,
    });
    await operationsResponse;
    await expect(page.getByLabel('Recommended refresh order')).toBeVisible({ timeout: 30_000 });

    const adminGridCols = await page.locator('.admin-grid').first().evaluate((el) => {
      return getComputedStyle(el).gridTemplateColumns
        .split(' ')
        .filter((x) => x.trim().length > 0).length;
    });
    expect(adminGridCols, 'admin summary cards should stack at phone width').toBe(1);

    const clipped = await page.evaluate(() => {
      const right = document.documentElement.clientWidth + 2;
      return Array.from(
        document.querySelectorAll(
          '.proto-hero, .admin-grid, .main__inner > .surface, .source-status-row, .meta-row, .meta-row__status',
        ),
      )
        .map((node) => {
          const rect = node.getBoundingClientRect();
          return {
            className: (node as HTMLElement).className,
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
          };
        })
        .filter((rect) => rect.left < 0 || rect.right > right);
    });
    expect(clipped, 'admin route content should not clip outside phone viewport').toEqual([]);
  });
});

/* ============================================================
   Responsive anchor matrix (container-query driven).

   Emits one test per (anchor, route) pair; each test boots the app
   at the anchor's viewport size, navigates, asserts the column
   count matches the expected band, and asserts the main content
   doesn't overflow the capped 1920px rule on ultra-wides.
   ============================================================ */
test.describe('Module 0 — responsive anchor matrix', () => {
  test.skip(!LIVE, 'Set E2E_LIVE=1 to run the responsive anchor matrix.');
  test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

  for (const anchor of ANCHORS) {
    test(`[${anchor.label}] Home renders ${anchor.kpiCols}-col KPI row, ${anchor.layoutACols}-col layoutA`, async ({ page }) => {
      await page.setViewportSize({ width: anchor.width, height: anchor.height });
      await page.goto('/');

      await expect(
        page.getByRole('heading', { name: /Who should we contact/i }),
      ).toBeVisible({ timeout: 15_000 });

      const kpiCols = await page.locator('.kpi-row').first().evaluate((el) => {
        return getComputedStyle(el).gridTemplateColumns
          .split(' ')
          .filter((x) => x.trim().length > 0).length;
      });
      expect(kpiCols, `Home .kpi-row at ${anchor.label}`).toBe(anchor.kpiCols);

      const layoutCols = await page.locator('.layoutA-grid').first().evaluate((el) => {
        return getComputedStyle(el).gridTemplateColumns
          .split(' ')
          .filter((x) => x.trim().length > 0).length;
      });
      expect(layoutCols, `Home .layoutA-grid at ${anchor.label}`).toBe(anchor.layoutACols);

      // Content cap: at ≥ 2001px viewport the .main__content wrapper
      // holds the text-dense content to 1920px. Home's wideMap opts
      // that wrapper up to 2400px; assert neither cap is exceeded.
      const contentWidth = await page
        .locator('.main__content')
        .first()
        .evaluate((el) => el.getBoundingClientRect().width);
      const cap = 2400; // Home uses wideMap
      expect(contentWidth, `Home .main__content width at ${anchor.label}`).toBeLessThanOrEqual(cap + 2);
    });

    test(`[${anchor.label}] Segment Intelligence renders ${anchor.segCols}-col seg-grid`, async ({ page }) => {
      await page.setViewportSize({ width: anchor.width, height: anchor.height });
      await page.goto('/segment-intelligence');

      await expect(page.locator('.seg-grid').first()).toBeVisible({ timeout: 30_000 });

      const segCols = await page.locator('.seg-grid').first().evaluate((el) => {
        return getComputedStyle(el).gridTemplateColumns
          .split(' ')
          .filter((x) => x.trim().length > 0).length;
      });
      expect(segCols, `Segment Intelligence .seg-grid at ${anchor.label}`).toBe(anchor.segCols);

      // Non-hero routes use the 1920px cap (no --wide-map).
      const contentWidth = await page
        .locator('.main__content')
        .first()
        .evaluate((el) => el.getBoundingClientRect().width);
      expect(contentWidth, `Segment Intelligence .main__content width at ${anchor.label}`).toBeLessThanOrEqual(1922);
    });

    test(`[${anchor.label}] Lead Queue content stays within the ultra-wide table cap`, async ({ page }) => {
      await page.setViewportSize({ width: anchor.width, height: anchor.height });
      await page.goto('/lead-queue');

      await expect(page.locator('.tbl').first()).toBeVisible({ timeout: 30_000 });

      // Content cap at 1920px; above 2001 container the .tbl surface caps at 1600.
      const contentWidth = await page
        .locator('.main__content')
        .first()
        .evaluate((el) => el.getBoundingClientRect().width);
      expect(contentWidth, `Lead Queue .main__content width at ${anchor.label}`).toBeLessThanOrEqual(1922);
    });

    test(`[${anchor.label}] Borrower 360 layoutA matches the anchor band`, async ({ page, request }) => {
      const target = await fetchFirstLeadId(request);
      await page.setViewportSize({ width: anchor.width, height: anchor.height });
      await page.goto(`/borrower-360/${target}`);

      // Borrower 360 has multiple .layoutA-grid blocks; just take the first.
      await expect(page.locator('.layoutA-grid').first()).toBeVisible({ timeout: 30_000 });

      await expectGridColumns(
        page.locator('.layoutA-grid').first(),
        anchor.layoutACols,
        `Borrower 360 .layoutA-grid at ${anchor.label}`,
      );
    });
  }
});

/* ============================================================
   Shell chrome scales with viewport
   ------------------------------------------------------------
   Rail width scales from 72 (default) → 80 (≥1920) → 96 (≥2560).
   Topbar grows from 56 → 64 at 2560+. These are viewport-scoped
   because the chrome sits against the viewport edge, not the
   main container.
   ============================================================ */
test.describe('Module 0 — shell chrome scales with viewport', () => {
  test.skip(!LIVE, 'Set E2E_LIVE=1 to run responsive/density coverage.');
  test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

  test('rail widens from 72 → 80 → 96 across 1440 / 1920 / 2560', async ({ page }) => {
    await page.goto('/');

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.locator('.rail')).toBeVisible();
    let railWidth = await page.locator('.rail').first().evaluate((el) => el.getBoundingClientRect().width);
    expect(railWidth).toBeGreaterThanOrEqual(60);
    expect(railWidth).toBeLessThanOrEqual(90);

    await page.setViewportSize({ width: 1920, height: 1080 });
    railWidth = await page.locator('.rail').first().evaluate((el) => el.getBoundingClientRect().width);
    expect(railWidth).toBeGreaterThanOrEqual(72);
    expect(railWidth).toBeLessThanOrEqual(92);

    await page.setViewportSize({ width: 2560, height: 1440 });
    railWidth = await page.locator('.rail').first().evaluate((el) => el.getBoundingClientRect().width);
    expect(railWidth).toBeGreaterThanOrEqual(84);
    expect(railWidth).toBeLessThanOrEqual(104);
  });
});
