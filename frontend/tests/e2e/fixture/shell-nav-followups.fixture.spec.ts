/**
 * Rendered-layer proofs for the W5c lane w5-shell-nav-followups (report
 * 12.4 #5, audit a11y-v2, flow-07, shell-09, critic-05), at 1440x900 unless
 * stated:
 *   - the measured route-nav dock's consumers: a glossary hash and the
 *     Administration section nav, docked (wrapped) and undocked;
 *   - the nested-scroller exclusions: the ZIP tile list is left out of the
 *     route nav's focus margin (measured, with its non-vacuity twin), and a
 *     census of the nested scrollers that hold a focusable element;
 *   - the route nav's two clusters for the admin, auditor and plain
 *     sessions (geometry and axe), and the item 3e measurement;
 *   - the presenter-only roadmap rail slots;
 *   - the Administration section nav's overflow cue (scrolling shadows).
 * Every test also proves the interaction opened no audited read (no VIEW_*
 * from a hover, a focus or a rail click).
 */
import type { Page } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import { expectAxeClean } from './axe';
import { AUDITOR_ONLY_SESSION, PRESENTER_SESSION, SESSION, WORKSPACE_USER_SESSION } from './data/shell';
import { json } from './mockApi';
import { asComputedRgb, parseRgb } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';
import { expect, test } from './test';

/**
 * Off Linux the nav's labels are widened toward Linux Chromium's wider text
 * (the LINUX_TEXT_EMULATION pattern of charts.fixture.spec.ts), so a nav
 * that wraps on CI wraps here too.
 */
const NAV_TEXT_EMULATION = '.route-nav__label { letter-spacing: 0.5px; }';

/** The selectors 38-focus-clearance.css leaves out of the route nav's margin. */
const EXCLUDED_SCROLLERS = ['.tbl-wrap', '.filter-menu', '.lead-approve-dialog', '.zip-tiles'] as const;

/**
 * Force the ZIP rung's tile fallback (integrator correction C4): a committed
 * ZCTA topology (w5-zcta-watchlist) would draw polygons instead, and the
 * tiles are what this lane's exclusion is about. An aborted request is not a
 * hygiene failure.
 */
async function forceZipTiles(page: Page): Promise<void> {
  await page.route('**/*.topo-*.json', (route) => route.abort('aborted'));
}

/** Viewport widths at a 768px height, widest first, to find one where the nav wraps and still docks. */
const WRAP_SEARCH_WIDTHS = [1024, 1000, 980, 960, 940, 920, 900, 880, 860, 840, 820, 800] as const;

/** Narrow the viewport (768px tall) until the route nav wraps while docked; returns the nav height. */
async function wrapDockedNav(page: Page): Promise<{ width: number; nav: number }> {
  if (process.platform !== 'linux') await page.addStyleTag({ content: NAV_TEXT_EMULATION });
  for (const width of WRAP_SEARCH_WIDTHS) {
    await page.setViewportSize({ width, height: 768 });
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const state = await page.locator('.route-nav').evaluate((nav) => ({
      nav: nav.getBoundingClientRect().height,
      docked: nav.hasAttribute('data-docked'),
    }));
    if (state.nav > 57 && state.docked) return { width, nav: state.nav };
  }
  throw new Error('precondition: no 768px-tall viewport wraps the route nav while it stays docked');
}

test.describe('the measured dock\'s consumers (report 12.4 #5)', () => {
  test('a glossary hash with the nav undocked lands the entry at .main\'s top', async ({ app, mockApi, page }) => {
    await page.setViewportSize({ width: 720, height: 450 });
    await app.gotoRoute('/glossary#ltv');
    const naturalLoad = markNaturalLoad(mockApi);
    expect(await page.locator('.route-nav').evaluate((nav) => nav.hasAttribute('data-docked')), 'precondition: undocked').toBe(false);
    const entry = page.locator('#ltv');
    await expect(entry).toBeVisible();
    await expect.poll(() => page.locator('.main').evaluate((main) => main.scrollTop), 'the scroller moved to the entry').toBeGreaterThan(0);
    const gap = await page.evaluate(() => {
      const main = document.querySelector<HTMLElement>('main.main');
      const target = document.getElementById('ltv');
      if (!main || !target) throw new Error('no .main or #ltv');
      return target.getBoundingClientRect().top - main.getBoundingClientRect().top;
    });
    expect(Math.abs(gap), `the entry sits at .main's top (gap ${gap}px): no clearance for a nav that scrolled away`).toBeLessThanOrEqual(1);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'glossary hash, undocked');
  });

  test('Administration\'s section nav sticks under a wrapped, docked route nav, and at .main\'s top once it undocks', async ({ app, mockApi, page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await app.gotoRoute('/admin-config');
    const naturalLoad = markNaturalLoad(mockApi);
    const { width, nav } = await wrapDockedNav(page);
    test.info().annotations.push({ type: 'wrapped-nav', description: `${width}x768: a ${nav}px route nav, docked` });
    const stuck = () => page.locator('.main').evaluate((main) => {
      main.scrollTop = main.scrollHeight;
      const section = main.querySelector<HTMLElement>('.admin-section-nav');
      const routeNav = main.querySelector<HTMLElement>(':scope > .route-nav');
      if (!section || !routeNav) throw new Error('no section nav or route nav');
      return {
        sectionTop: section.getBoundingClientRect().top,
        routeNavBottom: routeNav.getBoundingClientRect().bottom,
        mainTop: main.getBoundingClientRect().top,
        docked: routeNav.hasAttribute('data-docked'),
      };
    });
    const docked = await stuck();
    expect(docked.docked).toBe(true);
    expect(Math.abs(docked.sectionTop - docked.routeNavBottom), `flush under the ${nav}px route nav (${JSON.stringify(docked)})`).toBeLessThanOrEqual(1);

    await page.setViewportSize({ width: 1024, height: 768 });
    await app.openConsole();
    await expect.poll(async () => (await stuck()).docked, 'the bottom sheet undocks the route nav').toBe(false);
    const undocked = await stuck();
    expect(Math.abs(undocked.sectionTop - undocked.mainTop), `at .main's top (${JSON.stringify(undocked)})`).toBeLessThanOrEqual(1);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'admin section nav, docked and undocked');
  });
});

/** A ZIP tile list made to scroll (a short max height) with a tile placed 4px under its top. */
async function placeTileUnderListTop(page: Page): Promise<{ overflow: number; before: number }> {
  await page.addStyleTag({ content: '.zip-tiles { flex: none !important; max-block-size: 96px !important; }' });
  return page.locator('ul.zip-tiles').evaluate(async (list) => {
    const frames = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    await frames();
    const tiles = [...list.querySelectorAll<HTMLElement>('button.zip-tile[data-populated]')];
    const top = list.getBoundingClientRect().top;
    // The first tile of the second row, and the tile before it (end of row one).
    const second = tiles.find((tile) => tile.getBoundingClientRect().top > tiles[0].getBoundingClientRect().top + 1);
    if (!second) throw new Error('precondition: the tiles wrap to a second row');
    list.scrollTop += second.getBoundingClientRect().top - top - 4;
    await frames();
    const previous = tiles[tiles.indexOf(second) - 1];
    previous.focus({ preventScroll: true });
    return { overflow: list.scrollHeight - list.clientHeight, before: list.scrollTop };
  });
}

/**
 * The roving ArrowRight from the end of row one onto the first tile of row
 * two (fully in view, 4px under the list's top), the way a keyboard user
 * moves; returns how far the list scrolled and whether the arrow landed on
 * that tile.
 */
async function arrowOntoPlacedTile(page: Page, before: number): Promise<{ moved: number; onTile: boolean }> {
  await page.keyboard.press('ArrowRight');
  return page.locator('ul.zip-tiles').evaluate(async (list, start) => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    const active = document.activeElement;
    return {
      moved: Math.abs(list.scrollTop - start),
      onTile: active instanceof HTMLElement && active.matches('button.zip-tile') && list.contains(active),
    };
  }, before);
}

test.describe('nested scrollers are left out of the route nav\'s margin (a11y-v2)', () => {
  for (const restored of [false, true]) {
    test(`${restored ? 'non-vacuity, the nav margin restored inside it: ' : ''}a ZIP tile in view takes roving focus ${restored ? 'and the list over-scrolls' : 'without the list scrolling'}`, async ({ app, mockApi, page }) => {
      await forceZipTiles(page);
      await app.gotoRoute('/?geo_state=IL');
      const naturalLoad = markNaturalLoad(mockApi);
      await expect(page.locator('button.zip-tile').first()).toBeVisible();
      if (restored) await page.addStyleTag({ content: '.main .zip-tiles * { scroll-margin-block-start: var(--nav-clear) !important; }' });
      const placed = await placeTileUnderListTop(page);
      expect(placed.overflow, 'precondition: the tile list scrolls').toBeGreaterThan(40);
      const { moved, onTile } = await arrowOntoPlacedTile(page, placed.before);
      expect(onTile, 'the arrow moved focus to a tile').toBe(true);
      if (restored) expect(moved, 'the nav margin over-scrolls the list').toBeGreaterThan(40);
      else expect(moved, 'the list does not move').toBeLessThanOrEqual(1);
      expectNoAuditedReadSince(mockApi, naturalLoad, `zip tile roving focus${restored ? ' (twin)' : ''}`);
    });
  }

  interface Scroller {
    selector: string;
    focusable: number;
  }

  /** Every element in `.main` that scrolls (overflow auto/scroll) and holds a focusable element. */
  async function nestedScrollers(page: Page): Promise<Scroller[]> {
    return page.locator('.main').evaluate((main) => {
      const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex], [contenteditable="true"], summary';
      const scrolls = (value: string) => value === 'auto' || value === 'scroll';
      return [...main.querySelectorAll<HTMLElement>('*')]
        .filter((element) => {
          const style = getComputedStyle(element);
          return scrolls(style.overflowY) || scrolls(style.overflowX);
        })
        .map((element) => ({
          selector: `${element.tagName.toLowerCase()}.${[...element.classList].join('.')}`,
          focusable: element.querySelectorAll(FOCUSABLE).length,
        }))
        .filter((scroller) => scroller.focusable > 0);
    });
  }

  const excluded = (scroller: Scroller) => EXCLUDED_SCROLLERS.some((selector) => scroller.selector.split('.').includes(selector.slice(1)));

  for (const [width, height] of [[1440, 900], [600, 900]] as const) {
    test(`census at ${width}x${height}: every nested scroller with a focusable element is one the margin leaves out`, async ({ app, mockApi, page }) => {
      await page.setViewportSize({ width, height });
      await forceZipTiles(page);
      const found: Record<string, Scroller[]> = {};
      for (const path of ['/?geo_state=IL', '/segment-intelligence?geo_state=IL', '/lead-queue?states=IL,TX&zips=60601,60602']) {
        await app.gotoRoute(path);
        const naturalLoad = markNaturalLoad(mockApi);
        found[path] = await nestedScrollers(page);
        if (path.startsWith('/lead-queue')) {
          // The measured facts that need no exclusion: the scope strip holds
          // pills only, and the filter row never scrolls.
          const scope = page.locator('.lead-queue-scope');
          await expect(scope, 'precondition: the geo drill shows the scope strip').not.toHaveClass(/is-empty/);
          expect(await scope.evaluate((el) => el.querySelectorAll('a[href], button, input, select, textarea, [tabindex]').length)).toBe(0);
          const filterRow = await page.locator('.filter-row--lead-queue').first().evaluate((el) => ({
            overflowY: getComputedStyle(el).overflowY,
            container: el.closest('.main')?.clientWidth ?? 0,
          }));
          if (width <= 640) expect(filterRow.overflowY, 'the queue filter row does not scroll at a narrow container').toBe('visible');
        } else {
          await expect(page.locator('ul.zip-tiles'), `precondition: ${path} shows the ZIP tiles`).toBeVisible();
        }
        expectNoAuditedReadSince(mockApi, naturalLoad, `census ${path}`);
      }
      test.info().annotations.push({ type: 'nested-scrollers', description: JSON.stringify(found) });
      const unexcluded = Object.entries(found).flatMap(([path, list]) => list.filter((scroller) => !excluded(scroller)).map((scroller) => `${path}: ${scroller.selector}`));
      expect(unexcluded, 'a nested scroller with a focusable element the margin does not leave out').toEqual([]);
      expect(Object.values(found).flat().some((scroller) => scroller.selector.includes('zip-tiles')), 'non-vacuity: the census saw the tile list').toBe(true);
    });
  }
});

const SESSIONS = [
  ['admin', SESSION, 'Admin'],
  ['auditor', AUDITOR_ONLY_SESSION, 'Audit'],
  ['workspace user', WORKSPACE_USER_SESSION, 'Glossary'],
] as const;

test.describe('the route nav\'s two clusters (flow-07, shell-09)', () => {
  for (const theme of FIXTURE_THEMES) {
    for (const [who, session, lastTool] of SESSIONS) {
      test(`${theme} · ${who}: two named lists, the tools flush right, axe clean`, async ({ app, mockApi, page }) => {
        mockApi.register('GET', '/api/session', () => json<SessionResponse>(session));
        await app.setTheme(theme);
        await app.gotoRoute('/');
        const naturalLoad = markNaturalLoad(mockApi);
        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        await expect(nav.getByRole('list')).toHaveCount(2);
        await expect(nav.getByRole('list', { name: 'Lead workflow' }).getByRole('link')).toHaveCount(6);
        const tools = nav.getByRole('list', { name: 'Insight and reference' });
        await expect(tools.getByRole('link').last()).toHaveText(lastTool);
        const gap = await page.locator('.route-nav').evaluate((el) => {
          const style = getComputedStyle(el);
          const right = el.getBoundingClientRect().right - Number.parseFloat(style.paddingRight) - Number.parseFloat(style.borderRightWidth);
          return right - (el.querySelector('.route-nav__group--end')?.getBoundingClientRect().right ?? Number.NaN);
        });
        expect(Math.abs(gap), 'the tools cluster ends at the nav\'s content edge').toBeLessThanOrEqual(1);
        expect(await page.locator('.route-nav').evaluate((el) => Math.round(el.getBoundingClientRect().height))).toBe(57);
        // Intent preloads chunks (Analytics alone prefetches its aggregates): never an audited read.
        await tools.getByRole('link').first().hover();
        await nav.getByRole('link', { name: 'Leads' }).focus();
        await expectAxeClean(page, { key: { route: 'route-nav', state: `clusters-${who.replace(' ', '-')}` }, theme, known: {}, include: '.route-nav' });
        expectNoAuditedReadSince(mockApi, naturalLoad, `route-nav clusters · ${who}`);
      });
    }
  }

  /**
   * Report 12.4 #5 / item 3e: whether administrators may also get the Audit
   * link. They get it only if the nav stays one 57px line at 1440x900 with
   * the Console open; this clones it in and records the height (W5b
   * behaviour, no admin Audit link, is kept while it wraps). The integrator
   * re-measures after w5-field-vitals merges.
   */
  test('an eleventh (admin + Audit) link wraps the nav at 1440x900 with the Console open, so admins keep the ledger on the rail', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/');
    const naturalLoad = markNaturalLoad(mockApi);
    if (process.platform !== 'linux') await page.addStyleTag({ content: NAV_TEXT_EMULATION });
    await app.openConsole();
    const heights = await page.locator('.route-nav').evaluate(async (nav) => {
      const frames = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      const before = (nav as HTMLElement).offsetHeight;
      const admin = [...nav.querySelectorAll<HTMLElement>('.route-nav__item')].find((item) => item.textContent?.trim() === 'Admin');
      if (!admin) throw new Error('no Admin item');
      const audit = admin.cloneNode(true) as HTMLElement;
      const label = audit.querySelector('.route-nav__label');
      if (label) label.textContent = 'Audit';
      admin.before(audit);
      await frames();
      const after = (nav as HTMLElement).offsetHeight;
      audit.remove();
      return { before, after };
    });
    test.info().annotations.push({ type: 'item-3e', description: `admin nav, Console open: ${heights.before}px; with Audit cloned in: ${heights.after}px` });
    expect(heights.before, 'precondition: one line').toBe(57);
    expect(heights.after, 'an eleventh link wraps the one-line nav: admins keep the ledger on the rail and in the palette').toBeGreaterThan(57);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'item 3e measurement');
  });
});

test.describe('the roadmap rail slots show only in presenter mode (critic-05, D-shell-deviations-e2)', () => {
  /** Every request for the lazy RailRoadmap chunk, from now on. */
  function roadmapRequests(page: Page): string[] {
    const seen: string[] = [];
    page.on('request', (request) => {
      if (/\/RailRoadmap-[^/]*\.js(?:$|\?)/.test(request.url())) seen.push(request.url());
    });
    return seen;
  }

  test('a customer session shows M0 alone and never requests the roadmap chunk', async ({ app, mockApi, page }) => {
    const requested = roadmapRequests(page);
    await app.gotoRoute('/');
    const naturalLoad = markNaturalLoad(mockApi);
    const rail = page.getByRole('navigation', { name: 'Primary navigation' });
    await expect(rail.locator('.rail__item .mod')).toHaveText(['M0']);
    await expect(rail.locator('button')).toHaveCount(0);
    await rail.locator('.rail__item').first().hover();
    await rail.locator('.rail__item').first().click();
    await app.settle();
    expect(requested, 'the RailRoadmap chunk is never requested').toEqual([]);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'customer rail');
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: presenter mode shows M1-M4 as Tab-reachable disabled buttons with a keyboard tooltip, going nowhere`, async ({ app, mockApi, page }) => {
      mockApi.register('GET', '/api/session', () => json<SessionResponse>(PRESENTER_SESSION));
      const requested = roadmapRequests(page);
      await app.setTheme(theme);
      await app.gotoRoute('/');
      const naturalLoad = markNaturalLoad(mockApi);
      const rail = page.getByRole('navigation', { name: 'Primary navigation' });
      const slots = rail.locator('button.rail__item--disabled');
      await expect(slots).toHaveCount(4);
      expect(requested.length, 'presenter mode loads the chunk').toBeGreaterThan(0);
      await expect(rail.locator('.rail__item .mod')).toHaveText(['M0', 'M1', 'M2', 'M3', 'M4']);
      await expect(page.locator('#mip-tooltip')).toBeAttached();

      // Tab from M0 visits each slot in order; keyboard focus opens its tooltip.
      await rail.locator('a.rail__item.is-active').focus();
      for (let index = 0; index < 4; index += 1) {
        await page.keyboard.press('Tab');
        const slot = slots.nth(index);
        await expect(slot).toBeFocused();
        await expect(slot).toHaveAttribute('aria-disabled', 'true');
        await expect(slot).not.toHaveAttribute('title', /.*/);
        await expect(slot).toHaveAccessibleName(`M${index + 1}`);
        await expect(page.locator('#mip-tooltip[role="tooltip"]:not([hidden])')).toContainText(`Module ${index + 1}:`);
        await expect(page.locator('#mip-tooltip')).toContainText('On the roadmap; not part of this workspace.');
      }
      await expectAxeClean(page, { key: { route: 'home', state: 'presenter-rail' }, theme, known: {}, include: '.rail, #mip-tooltip' });

      // Activation goes nowhere and asks the API for nothing.
      const url = page.url();
      const calls = mockApi.calls.length;
      await page.keyboard.press('Enter');
      await page.keyboard.press('Space');
      await slots.first().click();
      await page.waitForTimeout(300);
      expect(page.url()).toBe(url);
      expect(mockApi.calls.slice(calls), 'no request from a roadmap slot').toEqual([]);
      expectNoAuditedReadSince(mockApi, naturalLoad, `presenter rail · ${theme}`);
    });
  }
});

/** The section nav's start- and end-edge pixels (2px down, in its top padding), from a real screenshot. */
async function edgePixels(page: Page): Promise<{ start: number[]; end: number[] }> {
  const png = await page.locator('.admin-section-nav').screenshot();
  return page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('2d canvas unavailable');
    context.drawImage(image, 0, 0);
    const at = (x: number) => [...context.getImageData(x, 2, 1, 1).data.slice(0, 3)];
    return { start: at(1), end: at(image.width - 2) };
  }, png.toString('base64'));
}

const sameRgb = (a: number[], b: number[], tolerance = 2) => a.every((value, index) => Math.abs(value - b[index]) <= tolerance);

test.describe('the Administration section nav shows where it overflows (W5b AL fix rounds; deviation:admin-section-nav-overflow-cue)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: an edge shadow marks only the side with more links, and the cue moves no layout`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/admin-config');
      const naturalLoad = markNaturalLoad(mockApi);
      const nav = page.locator('.admin-section-nav');
      // Independent of the section count (w5-field-vitals adds one): a spec-local
      // extra link and a narrow box make the nav overflow either way.
      await nav.evaluate((element) => {
        const last = element.lastElementChild;
        if (last) element.append(last.cloneNode(true));
        element.style.maxInlineSize = '420px';
        element.scrollLeft = 0;
      });
      const overflow = await nav.evaluate((element) => element.scrollWidth - element.clientWidth);
      expect(overflow, 'precondition: the section nav overflows sideways').toBeGreaterThan(100);
      const bg = parseRgb(await asComputedRgb(page, 'var(--bg-1)'));

      const atStart = await edgePixels(page);
      expect(sameRgb(atStart.start, bg), `at the start, the start edge is plain --bg-1 (${atStart.start} vs ${bg})`).toBe(true);
      expect(sameRgb(atStart.end, bg), `at the start, the end edge carries the shadow (${atStart.end})`).toBe(false);

      await nav.evaluate((element) => {
        element.scrollLeft = element.scrollWidth;
      });
      const atEnd = await edgePixels(page);
      expect(sameRgb(atEnd.end, bg), `at the end, the end edge is plain --bg-1 (${atEnd.end})`).toBe(true);
      expect(sameRgb(atEnd.start, bg), `at the end, the start edge carries the shadow (${atEnd.start})`).toBe(false);

      // Paint only: the block size is the same without the cue.
      const withCue = await nav.evaluate((element) => (element as HTMLElement).offsetHeight);
      await page.addStyleTag({ content: '.admin-section-nav { background: var(--bg-1) !important; }' });
      const withoutCue = await nav.evaluate((element) => (element as HTMLElement).offsetHeight);
      expect(withCue).toBe(withoutCue);
      expectNoAuditedReadSince(mockApi, naturalLoad, `admin section nav overflow cue · ${theme}`);
    });
  }
});
