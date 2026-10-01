/**
 * W5b w5-home-geo-lever, Home: rendered-layer proofs at 1440x900 against the
 * production build.
 *
 *  - WHY NOW (flow-05) lists the 30-year par move since the last visit FIRST,
 *    its FRED chip opening the rate window source, then the event measures;
 *    a first visit lists no rate row and does not read the rate window.
 *  - Watchlist briefings (wow-ai-4) say honestly that scheduled runs are off,
 *    and loading Home never POSTs to the Growth Agent (no run starts).
 *  - The briefing's age (states-09 / delivery-05): a snapshot restored after
 *    a reload says "Fetched 3h ago" while "Refreshing…", then "now"; a
 *    restored snapshot whose first refresh fails shows the failure, never
 *    the restored figures, and neither does one that failed before the
 *    lazy restore landed; a failed manual Refresh over data on screen shows
 *    the stale-data note with that data's age.
 *  - The Delta Explainer (wow-ai-3) opens from the refi trigger's chip, its
 *    table reconciles with its total, and nothing reads the attribution
 *    endpoint before the drawer opens (not on load, not on hover).
 *  - Home still never reads GET /api/leads or a borrower, the hero's right
 *    column stays one line, and exactly one primary button sits above the fold.
 */
import type { Locator, Page } from '@playwright/test';
import { HOME_SUMMARY, HOME_WATCHLIST_SUMMARY } from './data/portfolio';
import { RATE_MOVE_HOME_SUMMARY } from './data/homeAnswer';
import { json, type MockApi } from './mockApi';
import type { HomeSummary } from '../../../src/types';
import { expect, test } from './test';

const FOLD = 900;
const QUERY_CACHE_KEY = 'mip.queryCache.v1';
/** Home's hero reads: the two portfolio previews and the since-last-login summary. */
const HERO_READS = /\/api(?:\/v\d+)?\/(?:portfolio\/preview|home\/summary)(?:\?|$)/;
const QUERY_PERSIST_CHUNK = /\/assets\/queryPersist-[\w-]+\.js$/;
const ATTRIBUTION_READ = /\/home\/summary\/attribution$/;
const RATE_WINDOW_READ = /\/analytics\/rate-window$/;
const LEAD_LIST_READ = /^\/api(?:\/v\d+)?\/leads(?:\/|$)/;
const BORROWER_READ = /^\/api(?:\/v\d+)?\/borrowers\//;
const REFI = HOME_SUMMARY.highlights.find((highlight) => highlight.measure === 'refi_economics_screen');

const why = (page: Page) => page.locator('.home-answer .login-summary');
const triggers = (page: Page) => why(page).locator('.home-answer__trigger');

/** "+620" / "-31" / "0" (signedCount) back to a number. */
function signed(text: string | null): number {
  const cleaned = (text ?? '').replace(/[,\s]/g, '').replace(/−/g, '-');
  const value = Number(cleaned);
  if (!Number.isFinite(value)) throw new Error(`not a signed count: ${text}`);
  return value;
}

function serveSummary(mockApi: MockApi, summary: HomeSummary): void {
  mockApi.register('GET', '/api/home/summary', () => json<HomeSummary>(summary));
}

async function hoverAll(locator: Locator): Promise<void> {
  for (const element of await locator.all()) await element.hover();
}

test.describe('WHY NOW leads with the par move since the last visit', () => {
  test('the rate move is the first row, its FRED chip opens the rate window source', async ({ app, page, mockApi }) => {
    serveSummary(mockApi, RATE_MOVE_HOME_SUMMARY);
    await app.gotoRoute('/');
    const rate = why(page).locator('[data-testid="why-now-rate-move"]');
    await expect(rate).toBeVisible();
    await expect(triggers(page)).toHaveCount(1 + RATE_MOVE_HOME_SUMMARY.highlights.length);
    // First, then the two event measures, in the server's order.
    await expect(triggers(page).first()).toHaveAttribute('data-testid', 'why-now-rate-move');
    // data/rateWindow.ts: the week of the 2026-01-15 visit printed 6.41%, the latest (2026-04-13) 6.22%.
    await expect(rate.locator('.evidence-chip')).toHaveText('-19 bps');
    await expect(rate.getByRole('link', { name: '30-year par rate since your last visit (from 6.41% to 6.22%)' }))
      .toHaveAttribute('href', '/analytics');
    await expect(triggers(page).nth(1).getByRole('link')).toHaveText('borrowers with a listed home');
    await expect(triggers(page).nth(2).getByRole('link')).toHaveText('borrowers with a competitor lien');
    await expect(why(page).getByRole('link', { name: 'borrowers with a primary offer path' }))
      .toHaveAttribute('href', '/lead-queue?funnel_stage=offer_recommended');

    const drawer = await app.openEvidenceDrawer(rate.locator('.evidence-chip'));
    await expect(drawer).toContainText('30-year par rate since your last visit');
    await expect(drawer).toContainText('Week of 2026-01-12');
    await expect(drawer).toContainText('mip.gold.rate_window_weekly');
    await expect(drawer).toContainText('MORTGAGE30US');
  });

  test('a first visit lists no rate row and does not read the rate window', async ({ app, page, mockApi }) => {
    serveSummary(mockApi, { ...HOME_SUMMARY, status: 'first_visit', previous_visit_at: null, baseline_snapshot_at: null });
    await app.gotoRoute('/');
    await expect(why(page).locator('.home-answer__col-sub')).toHaveText('Welcome to your book');
    await expect(why(page).locator('[data-testid="why-now-rate-move"]')).toHaveCount(0);
    await app.settle();
    expect(mockApi.calls.filter((call) => RATE_WINDOW_READ.test(call.path))).toEqual([]);
  });
});

test.describe('Watchlist briefings', () => {
  test('the card says scheduled runs are off, and loading Home starts no run', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/');
    const card = page.locator('#main-content .watchlist-briefings');
    await expect(card.getByRole('heading', { name: 'Watchlist briefings' })).toBeVisible();
    await expect(card.locator('.watchlist-briefings__scheduler')).toHaveText('scheduled runs off');
    const rows = card.locator('[data-testid="watchlist-briefing"]');
    await expect(rows).toHaveCount(HOME_WATCHLIST_SUMMARY.watchlists.length);
    await expect(rows.first()).toContainText('Daily refi brief');
    await expect(rows.first()).toContainText('+12 since last run');
    await expect(rows.nth(1)).toContainText('Paused');
    await expect(rows.nth(1)).toContainText('first run');
    await hoverAll(card.locator('a'));
    await app.settle();
    const growthAgent = mockApi.calls.filter((call) => /\/growth-agent\//.test(call.path));
    expect(growthAgent.filter((call) => call.method !== 'GET'), 'Home load never POSTs to the Growth Agent').toEqual([]);
    expect(growthAgent.map((call) => call.path.replace(/^\/api\/v\d+/, '/api'))).toEqual(['/api/growth-agent/monitors/summary']);
  });
});

test.describe("the briefing's age", () => {
  async function loadAndSave(app: { gotoRoute(path: string): Promise<void> }, page: Page): Promise<void> {
    await page.clock.install({ time: new Date('2026-07-20T12:00:00Z') });
    await app.gotoRoute('/');
    await expect(triggers(page).first()).toBeVisible();
    // The persisted snapshot's 1 s trailing save (lib/queryPersist).
    await page.clock.runFor(1_500);
    await expect.poll(() => page.evaluate((key) => window.sessionStorage.getItem(key) !== null, QUERY_CACHE_KEY)).toBe(true);
    await page.clock.fastForward('03:00:00');
  }

  /** Hold Home's two hero reads at the network until `release()`; the mock API answers them then (degrade rules included). */
  async function holdHeroReads(page: Page): Promise<{ release: () => void }> {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(HERO_READS, async (route) => {
      await gate;
      await route.fallback();
    });
    return { release };
  }

  test('a restored briefing says how old it is while the fresh reads run, then "now"', async ({ app, page }) => {
    await loadAndSave(app, page);
    const held = await holdHeroReads(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    const fetched = page.locator('.proto-hero__actions [data-testid="fetched-at"]');
    await expect(fetched.locator('.fetched-at__label')).toHaveText('Fetched 3h ago');
    await expect(fetched.getByRole('button', { name: "Refresh today's briefing" })).toHaveText('Refreshing…');
    // The restored figures bridge the load.
    await expect(triggers(page).first()).toBeVisible();
    held.release();
    await expect(fetched.locator('.fetched-at__label')).toHaveText('Fetched now');
    await expect(fetched.getByRole('button', { name: "Refresh today's briefing" })).toHaveText('Refresh');
    await expect(page.locator('[data-testid="stale-data-note"]')).toHaveCount(0);
  });

  test('a restored briefing whose first refresh fails shows the failure, never the restored figures', async ({ app, page }) => {
    await loadAndSave(app, page);
    const held = await holdHeroReads(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    // The snapshot is on screen while the first refresh is in flight...
    await expect(triggers(page).first()).toBeVisible();
    await expect(page.locator('.proto-hero__actions .fetched-at__label')).toHaveText('Fetched 3h ago');
    // ...and that refresh fails (every retry).
    app.degrade('/api/portfolio/preview', { status: 500, body: { detail: 'fixture: preview is down' } });
    app.degrade('/api/home/summary', { status: 500, body: { detail: 'fixture: summary is down' } });
    held.release();
    await page.clock.runFor(30_000);
    await expect(page.locator('#main-content').getByText("Couldn't load portfolio KPIs.")).toBeVisible();
    await expect(page.locator('.kpi .kpi__value').first()).toHaveText('—');
    await expect(triggers(page)).toHaveCount(0);
    await expect(page.locator('.home-answer .evidence-chip', { hasText: REFI?.display ?? '+2,250' })).toHaveCount(0);
    await expect(page.locator('[data-testid="stale-data-note"]')).toHaveCount(0);
  });

  test('a refresh that fails before the snapshot restore lands never brings the restored figures back', async ({ app, page }) => {
    // lib/queryPersist screens a read that already failed out of the snapshot
    // right before the newer-wins hydrate (delivery-05 / R1).
    await loadAndSave(app, page);
    let releaseRestore: () => void = () => undefined;
    const restoreHeld = new Promise<void>((resolve) => {
      releaseRestore = resolve;
    });
    await page.route(QUERY_PERSIST_CHUNK, async (route) => {
      await restoreHeld;
      await route.fallback();
    });
    app.degrade('/api/portfolio/preview', { status: 500, body: { detail: 'fixture: preview is down' } });
    app.degrade('/api/home/summary', { status: 500, body: { detail: 'fixture: summary is down' } });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.clock.runFor(30_000);
    await expect(page.locator('#main-content').getByText("Couldn't load portfolio KPIs.")).toBeVisible();
    const restored = page.waitForResponse(QUERY_PERSIST_CHUNK);
    releaseRestore();
    await restored;
    await page.clock.runFor(2_000);
    await expect(page.locator('#main-content').getByText("Couldn't load portfolio KPIs.")).toBeVisible();
    await expect(triggers(page)).toHaveCount(0);
  });

  test('a failed manual Refresh over the briefing marks it with the data\'s age', async ({ app, page }) => {
    await app.gotoRoute('/');
    await expect(triggers(page).first()).toBeVisible();
    await expect(page.locator('[data-testid="stale-data-note"]')).toHaveCount(0);
    app.degrade('/api/home/summary', { status: 500, body: { detail: 'fixture: summary is down' } });
    await page.locator('.proto-hero__actions').getByRole('button', { name: "Refresh today's briefing" }).click();
    const note = page.locator('#main-content [data-testid="stale-data-note"]');
    await expect(note).toHaveCount(1, { timeout: 20_000 });
    await expect(note).toContainText('Showing counts last read');
    await expect(note).toContainText('The latest refresh failed');
    // The data on screen stays; the note only marks it.
    await expect(triggers(page).first()).toBeVisible();
  });
});

test.describe('the Delta Explainer', () => {
  test('opens from the refi trigger chip, reconciles, and reads nothing before the drawer opens', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/');
    await expect(triggers(page)).toHaveCount(HOME_SUMMARY.highlights.length);
    await hoverAll(why(page).locator('.evidence-chip, a'));
    await app.settle();
    expect(mockApi.calls.filter((call) => ATTRIBUTION_READ.test(call.path)), 'no attribution read before the drawer').toEqual([]);

    const chip = why(page).locator('.evidence-chip', { hasText: REFI?.display ?? '+2,250' });
    const drawer = await app.openEvidenceDrawer(chip);
    const explainer = drawer.locator('[data-testid="delta-explainer"]');
    await expect(explainer).toBeVisible();
    await expect(explainer.locator('table tbody tr')).not.toHaveCount(0);
    const changes = await explainer.locator('table tbody tr td:last-child').allTextContents();
    const total = signed(await explainer.locator('[data-testid="delta-explainer-total"]').textContent());
    expect(changes.map(signed).reduce((sum, value) => sum + value, 0)).toBe(total);
    await expect(explainer.locator('table tbody tr[data-step="unattributed"]')).toHaveCount(1);
    await expect(explainer.locator('[data-testid="delta-explainer-reconcile"]')).toContainText(REFI?.display ?? '+2,250');
    const reads = mockApi.calls.filter((call) => ATTRIBUTION_READ.test(call.path));
    expect(reads).toHaveLength(1);
    expect(new URLSearchParams(reads[0].search).get('measure')).toBe('refi_economics_screen');
    expect(new URLSearchParams(reads[0].search).get('baseline')).toBe('2026-07-09');
  });
});

test.describe('Home reads and hero', () => {
  test('Home never reads the lead list or a borrower, with the briefings and the rate move loaded', async ({ app, page, mockApi }) => {
    serveSummary(mockApi, RATE_MOVE_HOME_SUMMARY);
    await app.gotoRoute('/');
    await expect(why(page).locator('[data-testid="why-now-rate-move"]')).toBeVisible();
    await expect(page.locator('[data-testid="watchlist-briefing"]').first()).toBeVisible();
    await hoverAll(page.locator('#main-content .home-answer a, #main-content .watchlist-briefings a'));
    await app.settle();
    const reads = mockApi.calls.filter((call) => call.method === 'GET');
    expect(reads.filter((call) => LEAD_LIST_READ.test(call.path)), 'GET /api/leads writes VIEW_LEADS').toEqual([]);
    expect(reads.filter((call) => BORROWER_READ.test(call.path)), 'borrower reads write VIEW_BORROWER').toEqual([]);
  });

  test('the hero actions stay one line and exactly one primary button is above the fold', async ({ app, page }) => {
    await app.gotoRoute('/');
    await expect(page.locator('.proto-hero__actions [data-testid="fetched-at"] .fetched-at__label')).toBeVisible();
    const rows = await page.locator('.proto-hero__actions > *').evaluateAll((nodes) =>
      nodes
        .map((node) => node.getBoundingClientRect())
        .filter((rect) => rect.width > 0)
        .map((rect) => ({ top: rect.top, bottom: rect.bottom, height: rect.height })),
    );
    expect(rows.length).toBeGreaterThanOrEqual(3);
    const minHeight = Math.min(...rows.map((row) => row.height));
    const tops = rows.map((row) => row.top + row.height / 2);
    expect(Math.max(...tops) - Math.min(...tops), 'every hero action centres on one row').toBeLessThan(minHeight / 2);
    const primaries = await page.locator('#main-content .btn--primary').evaluateAll((nodes, fold) =>
      nodes
        .filter((node) => {
          const rect = node.getBoundingClientRect();
          return rect.width > 0 && rect.top < fold;
        })
        .map((node) => node.textContent?.trim()),
    FOLD);
    expect(primaries).toEqual(["Review today's top leads"]);
  });
});
