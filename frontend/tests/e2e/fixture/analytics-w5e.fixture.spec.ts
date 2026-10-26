/**
 * W5e w5-analytics at the rendered layer, 1440x900 against the fixture
 * harness (the production build):
 *
 *  - per-tab chunks (bundle-06): the Executive default loads no other tab's
 *    module; Economics loads the sections and scatter chunks; hovering a tab
 *    warms its chunk and starts no read;
 *  - no DOM title attribute under main on any tab (critic-08), with no
 *    exclusion: the Timestamp's UTC form is a ui/Tooltip description;
 *  - the scatter tip on hover and on keyboard focus (dataviz-07 a);
 *  - the index-0 tip a pointer press made hides on pointerleave (item 4);
 *  - FetchedAt: Refresh issues exactly one GET per read, nothing audited
 *    across load, tab switches, Refresh and borrower-link focus (states-09);
 *  - a failed Refresh keeps the data and shows the stale note;
 *  - a restored snapshot says how old it is while its fresh read runs, and a
 *    failed first refresh of it shows the real failure (delivery-05);
 *  - the hero keeps its height across the first load, placeholder data and
 *    the loaded state;
 *  - an Executive KPI drawer leads with "How we got {value}" and one GET
 *    /api/kpi-proof (flow-06).
 */
import type { Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import { executiveKpiChip } from './analyticsW5eStates';
import type { MockApi } from './mockApi';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const LAZY_CHUNK = /\/assets\/analytics\.(sections|equity-scatter|approval-funnel|sales-ops)-[\w-]+\.js$/;
const QUERY_CACHE_KEY = 'mip.queryCache.v1';
const EXECUTIVE_READS = /\/api(?:\/v\d+)?\/analytics\/(?:executive|rate-window)(?:\?|$)/;
const TABS = ['executive', 'geography', 'economics', 'segments', 'signals', 'approval-funnel', 'sales-ops'] as const;

/** Every lazy Analytics chunk the page requests, by module name. */
function chunkRequests(page: Page): string[] {
  const seen: string[] = [];
  page.on('request', (request) => {
    const match = LAZY_CHUNK.exec(request.url());
    if (match) seen.push(match[1]);
  });
  return seen;
}

/** GET calls whose path ends with `suffix` (any API version prefix). */
function gets(mockApi: MockApi, suffix: string): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path.endsWith(suffix)).length;
}

const hero = (page: Page) => page.locator('.proto-hero__actions [data-testid="fetched-at"]');

test.describe('per-tab chunks (bundle-06)', () => {
  test('the Executive default loads no other tab module; Economics loads the sections and scatter chunks', async ({ app, page }) => {
    const chunks = chunkRequests(page);
    await app.gotoRoute('/analytics');
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    await app.settle();
    expect(chunks).toEqual([]);

    await page.getByRole('tab', { name: 'Economics' }).click();
    await expect(page.locator('#main-content [data-testid="equity-spread-scatter"] .analytics-scatter__bin').first()).toBeVisible();
    expect([...new Set(chunks)].sort()).toEqual(['equity-scatter', 'sections']);
  });

  test('hovering a tab warms its chunk and starts no read', async ({ app, mockApi, page }) => {
    const chunks = chunkRequests(page);
    await app.gotoRoute('/analytics');
    await app.settle();
    const before = mockApi.calls.length;
    await page.getByRole('tab', { name: 'Sales ops' }).hover();
    await expect.poll(() => chunks.includes('sales-ops')).toBe(true);
    await app.settle();
    const reads = mockApi.calls.slice(before).filter((call) => /\/(?:analytics|sales)\//.test(call.path));
    expect(reads, 'a warmed tab reads nothing until it is selected').toEqual([]);
    await expect(page.getByRole('tab', { name: 'Executive' })).toHaveAttribute('aria-selected', 'true');
  });
});

test('no tab carries a DOM title attribute under main (critic-08)', async ({ app, page }) => {
  // Seven route loads in one case.
  test.slow();
  for (const tab of TABS) {
    await app.gotoRoute(tab === 'executive' ? '/analytics' : `/analytics?view=${tab}`);
    await expect(page.locator('#main-content [data-analytics-skeleton]')).toHaveCount(0);
    await app.settle();
    // No exclusion since the W5e compiler-lint residual: the Timestamp's
    // absolute UTC form and the Ask Genie icon button's prompt are ui/Tooltip
    // descriptions now, never a native title.
    const titled = await page.locator('#main-content [title]').evaluateAll((nodes) =>
      nodes.map((node) => `${node.tagName.toLowerCase()}.${node.className}`),
    );
    expect(titled, `${tab}: title attributes under main`).toEqual([]);
  }
});

test.describe('the scatter tip (dataviz-07 a)', () => {
  test('shows on hover and on keyboard focus, and never takes the pointer', async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=economics');
    const bins = page.locator('#main-content .analytics-scatter__bin');
    await expect(bins.first()).toBeVisible();
    const tip = page.getByTestId('scatter-tip');

    await bins.first().hover();
    await expect(tip).toBeVisible();
    await expect(tip.locator('.analytics-chart__tip-x')).toHaveText(/borrowers$/);
    await expect(tip.locator('.analytics-chart__tip-y')).toHaveText(/^mean score \d+ · .+ in the money$/);
    expect(await tip.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('none');
    await page.mouse.move(0, 0);
    await expect(tip).toHaveCount(0);

    await bins.first().focus();
    await expect(tip).toBeVisible();
    await page.keyboard.press('ArrowRight');
    await expect(bins.nth(1)).toBeFocused();
    await expect(tip).toBeVisible();
    await expect(page.locator('#main-content .analytics-scatter__guide')).not.toHaveCount(0);
  });
});

test('a plot a pointer press focused hides its index-0 tip on pointerleave (item 4)', async ({ app, page }) => {
  await app.gotoRoute('/analytics');
  const plot = page.getByRole('group', { name: 'Opportunity Score Distribution', exact: true });
  await expect(plot).toBeVisible();
  await plot.scrollIntoViewIfNeeded();
  const box = await plot.boundingBox();
  if (!box) throw new Error('no score plot');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(plot).toBeFocused();
  const tip = plot.locator('.analytics-chart__tip');
  await expect(tip).toBeVisible();
  await page.mouse.move(0, 0);
  await expect(tip, 'the pointer left: no tip stays on the first bin').toHaveCount(0);
  // Keyboard focus still shows the cursor.
  await page.keyboard.press('ArrowRight');
  await expect(tip).toBeVisible();
});

test.describe('fetch age, Refresh and the stale note (states-09)', () => {
  test('Refresh issues exactly one GET per read, and nothing audited runs across load, tabs, Refresh and link focus', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/analytics');
    const naturalLoad = markNaturalLoad(mockApi);
    await expect(hero(page).locator('.fetched-at__label')).toContainText('Fetched');
    const executive = gets(mockApi, '/analytics/executive');
    const rateWindow = gets(mockApi, '/analytics/rate-window');
    const refresh = hero(page).getByRole('button', { name: 'Refresh executive analytics' });
    await refresh.click();
    await expect(refresh).toHaveText('Refresh');
    await app.settle();
    expect(gets(mockApi, '/analytics/executive') - executive).toBe(1);
    expect(gets(mockApi, '/analytics/rate-window') - rateWindow).toBe(1);

    await page.getByRole('tab', { name: 'Economics' }).click();
    await expect(hero(page).getByRole('button', { name: 'Refresh economics analytics' })).toBeVisible();
    const link = page.locator('#main-content a[href^="/borrower-360/"]').first();
    await link.focus();
    await link.hover();
    await page.getByRole('tab', { name: 'Approval funnel' }).click();
    await expect(page.locator('#main-content [data-testid="fetched-at"], .proto-hero__actions [data-testid="fetched-at"]')).toHaveCount(0);
    await app.settle();
    expectNoAuditedReadSince(mockApi, naturalLoad, 'analytics: tabs, Refresh and borrower-link focus');
  });

  test('a failed Refresh keeps the data and shows the stale note', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/analytics');
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    mockApi.degrade('/api/analytics/executive', { status: 500, method: 'GET', body: { detail: 'fixture: executive refresh failed' } });
    await hero(page).getByRole('button', { name: 'Refresh executive analytics' }).click();
    await expect(page.locator('#main-content [data-testid="stale-data-note"]')).toHaveCount(1);
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    await expect(page.locator('#main-content')).not.toContainText('fixture: executive refresh failed');
  });

  test('the hero keeps its height across the first load, placeholder data and the loaded state', async ({ page }) => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let hold = true;
    await page.route(EXECUTIVE_READS, async (route) => {
      if (hold) await gate;
      await route.fallback();
    });
    await page.goto('/analytics', { waitUntil: 'domcontentloaded' });
    const heroBox = page.locator('.proto-hero');
    await expect(hero(page).getByRole('button', { name: 'Refresh executive analytics' })).toHaveText('Refreshing…');
    const firstLoad = (await heroBox.boundingBox())?.height;
    release();
    hold = false;
    await expect(hero(page).locator('.fetched-at__label')).toContainText('Fetched');
    const loaded = (await heroBox.boundingBox())?.height;
    // A filter change keeps the previous rows as placeholder data while the new key reads.
    let releaseFilter: () => void = () => undefined;
    const filterGate = new Promise<void>((resolve) => {
      releaseFilter = resolve;
    });
    hold = true;
    await page.unroute(EXECUTIVE_READS);
    await page.route(EXECUTIVE_READS, async (route) => {
      if (hold) await filterGate;
      await route.fallback();
    });
    await page.getByRole('button', { name: /^State:/ }).click();
    await page.getByRole('option', { name: 'IL', exact: true }).click();
    await expect(hero(page).getByRole('button', { name: 'Refresh executive analytics' })).toHaveText('Refreshing…');
    const placeholder = (await heroBox.boundingBox())?.height;
    releaseFilter();
    hold = false;
    expect(firstLoad).toBeDefined();
    expect(loaded).toBe(firstLoad);
    expect(placeholder).toBe(firstLoad);
  });
});

test.describe('restored age (delivery-05)', () => {
  async function loadAndSave(app: { gotoRoute(path: string): Promise<void> }, page: Page): Promise<void> {
    await page.clock.install({ time: new Date('2026-07-20T12:00:00Z') });
    await app.gotoRoute('/analytics');
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    await page.clock.runFor(1_500);
    await expect.poll(() => page.evaluate((key) => window.sessionStorage.getItem(key) !== null, QUERY_CACHE_KEY)).toBe(true);
    await page.clock.fastForward('03:00:00');
  }

  async function holdExecutiveReads(page: Page): Promise<{ release: () => void }> {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(EXECUTIVE_READS, async (route) => {
      await gate;
      await route.fallback();
    });
    return { release };
  }

  test('a restored snapshot shows its own age while the fresh read runs', async ({ app, page }) => {
    await loadAndSave(app, page);
    const held = await holdExecutiveReads(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(hero(page).locator('.fetched-at__label')).toHaveText('Fetched 3h ago');
    await expect(hero(page).getByRole('button', { name: 'Refresh executive analytics' })).toHaveText('Refreshing…');
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    held.release();
    await expect(hero(page).locator('.fetched-at__label')).toHaveText('Fetched now');
    await expect(page.locator('#main-content [data-testid="stale-data-note"]')).toHaveCount(0);
  });

  test('a failed first refresh of a restored snapshot shows the real failure, not the snapshot', async ({ app, page }) => {
    await loadAndSave(app, page);
    const held = await holdExecutiveReads(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(hero(page).locator('.fetched-at__label')).toHaveText('Fetched 3h ago');
    app.degrade('/api/analytics/executive', { status: 500, method: 'GET', body: { detail: 'fixture: executive is down' } });
    held.release();
    await page.clock.runFor(30_000);
    await expect(page.locator('#main-content [data-async-status]').first()).toBeVisible();
    await expect(page.locator('#main-content .funnel-sankey')).toHaveCount(0);
    await expect(page.locator('#main-content')).not.toContainText('fixture: executive is down');
  });
});

test.describe('Executive KPI evidence (flow-06)', () => {
  for (const theme of ['dark', 'light'] as const) {
    test(`${theme}: an Executive KPI drawer leads with How we got and one audit-free proof read`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/analytics');
      const naturalLoad = markNaturalLoad(mockApi);
      const proofs = () => mockApi.calls.filter((call) => call.path.endsWith('/kpi-proof'));
      const drawer = await app.openEvidenceDrawer(executiveKpiChip(page));
      const how = drawer.locator('[data-testid="evidence-how-we-got"]');
      await expect(how.getByRole('heading', { level: 3 })).toHaveText(/^How we got [\d,]+$/);
      await expect(how).toContainText('Every borrower in the addressable book');
      await drawer.getByRole('tab', { name: 'Under the hood' }).click();
      await expect(drawer.locator('[data-testid="evidence-kpi-proof"] .proof-sql-card__sql')).toContainText('borrower_lifecycle_state');
      await app.settle();
      expect(proofs()).toHaveLength(1);
      expect(proofs()[0].search).toContain('kpi=analytics.addressable');
      await expectAxeClean(page, { key: { route: 'analytics-executive', state: 'executive-evidence-flow' }, theme, known: {}, include: 'dialog.drawer' });
      expectNoAuditedReadSince(mockApi, naturalLoad, 'analytics: an Executive KPI drawer');
    });
  }

  test('a filtered Executive card opens its source without a proof', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/analytics?states=IL');
    const drawer = await app.openEvidenceDrawer(executiveKpiChip(page));
    await expect(drawer.locator('[data-testid="evidence-how-we-got"]')).toHaveCount(0);
    await app.settle();
    expect(mockApi.calls.filter((call) => call.path.endsWith('/kpi-proof'))).toEqual([]);
  });
});
