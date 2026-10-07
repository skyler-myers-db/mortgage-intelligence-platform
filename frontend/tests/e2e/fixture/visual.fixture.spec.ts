/**
 * Pixel gate (audit visual-10, quality-02, responsive-v2): toHaveScreenshot
 * baselines of the production build against the fixture API, at 1440x900
 * unless stated, in both themes.
 *
 *  A. every FIXTURE_ROUTES entry, Console closed                      40
 *  B. the nine `product` routes with the Console open                 18
 *  C. shell states: Home evidence drawer, command palette, Genie
 *     panel and degraded; Lead Queue expanded row, empty and the
 *     Triage deck; Administration's Run confirm                       16
 *  D. the second `.main` page of Home, Borrower 360, Offer detail      6
 *  E. Lead Queue in compact density                                    2
 *  F. 1280x720: Home, Lead Queue, Borrower 360                         6
 *  G. accent sweep: the first Home KPI and the map legend,
 *     teal / navy / red (bright is A-F)                               12
 *  H. read-failed: Lead Queue, Segments                                4
 *  I. W5b: Home's Delta Explainer drawer, Segments' stale note,
 *     Home's WHY NOW rate move and the watchlist briefings card        8
 *                                                                     --
 *                                                                    112
 *
 * Baselines are amd64-Linux renders from the pinned Playwright container:
 * this spec runs only with MIP_VRT=1 (playwright.config.ts) and refuses any
 * other host. Regenerate or compare them with tools/update_visual_baselines.sh
 * (docs/testing.md, "Visual regression"). Nothing is masked: the clock is
 * frozen, motion is reduced and disabled, fonts are self-hosted and there is
 * no canvas. Every captured state also asserts that no `.surface` scrolls
 * sideways and that no state opened an audited read.
 */
import type { Locator, Page } from '@playwright/test';
import type { AppDriver, FixtureAccent, FixtureTheme } from './app';
import { enterState, prepareState, type FixtureState } from './fixtureStates';
import type { HomeSummary } from '../../../src/types';
import { RATE_MOVE_HOME_SUMMARY } from './data/homeAnswer';
import { json, type MockApi } from './mockApi';
import { FIXTURE_ROUTES, FIXTURE_THEMES, type FixtureRoute } from './routes';
import { expect, test } from './test';
import {
  assertPinnedVrtHost,
  capture,
  expectNoAuditedReadSince,
  expectNoSurfaceOverflow,
  markNaturalLoad,
  scrollMainBy,
} from './visual';

// Guard in a hook, not at module load, so `--list` still collects the
// matrix on any host; every test fails before it captures anything.
test.beforeAll(({}, testInfo) => {
  assertPinnedVrtHost(testInfo.config.configFile);
});

const SECOND_PAGE_ROUTES = new Set(['home', 'borrower-360-detail', 'offer-orchestrator-detail']);

/**
 * The one masked region, in the two captures that show it (Home's second
 * page and the legend specimens). Cause, proven by the double run: the map
 * legend is a `backdrop-filter` layer anchored at a fractional offset
 * (y 1260.766 at 1440x900) and its caption line lands at y 1286.016, 0.016px
 * past a pixel boundary, so the compositor's raster translation of that layer
 * rounds the caption up or down from run to run (a 1px vertical shift; 353
 * and 395 px differed in dark and light). The rest of the legend (ramp,
 * breaks, labels) stays unmasked, and the caption's copy is asserted before
 * each capture instead.
 */
async function legendCaptionMask(page: Page) {
  const caption = page.locator('.map-legend__caption').first();
  await expect(caption).toContainText('Colored by: marketable population');
  return [caption];
}
const ROUTE_BY_NAME = new Map(FIXTURE_ROUTES.map((route) => [route.name, route]));

function route(name: string): FixtureRoute {
  const found = ROUTE_BY_NAME.get(name);
  if (!found) throw new Error(`no fixture route named ${name}`);
  return found;
}

/** Overflow check plus capture for one state; `file` is the baseline's base name. */
async function check(
  page: Page,
  routeName: string,
  theme: FixtureTheme,
  state: string,
  options: { file?: string; mask?: Locator[] } = {},
): Promise<void> {
  await expectNoSurfaceOverflow(page, { route: routeName, state, theme });
  await capture(page, options.file ?? `${routeName}--${theme}--${state}.png`, { mask: options.mask });
}

interface Loaded {
  naturalLoad: number;
}

async function load(app: AppDriver, mockApi: MockApi, target: FixtureRoute, theme: FixtureTheme, state: FixtureState = 'default'): Promise<Loaded> {
  await app.setTheme(theme);
  prepareState(mockApi, state, target.name);
  await app.gotoRoute(target.path);
  return { naturalLoad: markNaturalLoad(mockApi) };
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    // A + D + B: one page load per route.
    for (const target of FIXTURE_ROUTES) {
      test(`${target.name} · default${SECOND_PAGE_ROUTES.has(target.name) ? ', second page' : ''}${target.product ? ', Console open' : ''}`, async ({ app, mockApi, page }) => {
        const { naturalLoad } = await load(app, mockApi, target, theme);
        await check(page, target.name, theme, 'default');

        if (SECOND_PAGE_ROUTES.has(target.name)) {
          // The app scrolls inside .main, so fullPage does nothing: scroll it.
          await scrollMainBy(page, 1);
          await app.settle();
          const mask = target.name === 'home' ? await legendCaptionMask(page) : undefined;
          await check(page, target.name, theme, 'second-page', { mask });
          await scrollMainBy(page, 0);
          await app.settle();
        }

        if (target.product) {
          await app.openConsole();
          await app.settle();
          await check(page, target.name, theme, 'console');
        }
        expectNoAuditedReadSince(mockApi, naturalLoad, target.name);
      });
    }

    // C: shell states.
    const SHELL_STATES: ReadonlyArray<{ route: string; state: FixtureState }> = [
      { route: 'home', state: 'evidence-drawer' },
      { route: 'home', state: 'command-palette' },
      { route: 'home', state: 'genie' },
      { route: 'home', state: 'degraded' },
      { route: 'lead-queue', state: 'expanded-row' },
      { route: 'lead-queue', state: 'empty' },
      { route: 'lead-queue', state: 'triage' },
      // H: the non-bannered failed read (quality-06), health OK.
      { route: 'lead-queue', state: 'read-failed' },
      { route: 'segment-intelligence', state: 'read-failed' },
      // critic-09: the Data operations Run confirm.
      { route: 'admin-config', state: 'run-dialog-open' },
      // W5b w5-home-geo-lever: the Delta Explainer drawer and the retained-value note.
      { route: 'home', state: 'delta-explainer' },
      { route: 'segment-intelligence', state: 'stale-note' },
    ];
    for (const { route: name, state } of SHELL_STATES) {
      test(`${name} · ${state}`, async ({ app, mockApi, page }) => {
        const { naturalLoad } = await load(app, mockApi, route(name), theme, state);
        await enterState(app, page, state);
        await check(page, name, theme, state);
        expectNoAuditedReadSince(mockApi, naturalLoad, `${name} · ${state}`);
      });
    }

    // I (W5b w5-home-geo-lever): WHY NOW led by the par move since the last
    // visit, and the watchlist briefings card further down Home.
    test('home · why now rate move, watchlist briefings', async ({ app, mockApi, page }) => {
      mockApi.register('GET', '/api/home/summary', () => json<HomeSummary>(RATE_MOVE_HOME_SUMMARY));
      const { naturalLoad } = await load(app, mockApi, route('home'), theme);
      await expect(page.locator('[data-testid="why-now-rate-move"]')).toBeVisible();
      await check(page, 'home', theme, 'why-now-rate-move');
      const briefings = page.locator('#main-content .watchlist-briefings');
      await expect(briefings.locator('[data-testid="watchlist-briefing"]').first()).toBeVisible();
      await briefings.scrollIntoViewIfNeeded();
      await app.settle();
      await capture(page, `home--${theme}--watchlist-briefings.png`, { element: briefings });
      expectNoAuditedReadSince(mockApi, naturalLoad, 'home · why now rate move');
    });

    // E: compact density.
    test('lead-queue · compact density', async ({ app, mockApi, page }) => {
      await app.setDensity('compact');
      const { naturalLoad } = await load(app, mockApi, route('lead-queue'), theme);
      await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
      await check(page, 'lead-queue', theme, 'compact');
      expectNoAuditedReadSince(mockApi, naturalLoad, 'lead-queue · compact');
    });

    // F: a 1280x720 laptop.
    for (const name of ['home', 'lead-queue', 'borrower-360-detail']) {
      test(`${name} · 1280x720`, async ({ app, mockApi, page }) => {
        await page.setViewportSize({ width: 1280, height: 720 });
        const { naturalLoad } = await load(app, mockApi, route(name), theme);
        await check(page, name, theme, 'default', { file: `${name}--${theme}--1280x720.png` });
        expectNoAuditedReadSince(mockApi, naturalLoad, `${name} · 1280x720`);
      });
    }

    // G: accent specimens (bright is covered by A-F).
    for (const accent of ['teal', 'navy', 'red'] as const satisfies readonly FixtureAccent[]) {
      test(`home · accent ${accent} specimens`, async ({ app, mockApi, page }) => {
        await app.setAccent(accent);
        const { naturalLoad } = await load(app, mockApi, route('home'), theme);
        await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
        await expectNoSurfaceOverflow(page, { route: 'home', state: 'default', theme });
        const kpi = page.locator('.kpi').first();
        await expect(kpi.locator('.kpi__value')).toBeVisible();
        await expect(kpi.locator('.evidence-chip')).toBeVisible();
        await capture(page, `home--${theme}--accent-${accent}-kpi.png`, { element: kpi });
        const legend = page.locator('.map-legend').first();
        await expect(legend).toBeVisible();
        await capture(page, `home--${theme}--accent-${accent}-map-legend.png`, { element: legend, mask: await legendCaptionMask(page) });
        expectNoAuditedReadSince(mockApi, naturalLoad, `home · accent ${accent}`);
      });
    }
  });
}
