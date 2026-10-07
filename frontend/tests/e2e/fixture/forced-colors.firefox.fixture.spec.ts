/**
 * Forced colors in Firefox, automated (manual check 2026-09-30, audit
 * a11y-10 item 4 / css-06 item 3). Firefox implements forced colors itself:
 * the fixture-firefox-forced project (MIP_CROSS_ENGINE=1) launches it with
 * `browser.display.document_color_use = 2` ("Override colors: Always"), so
 * this spec replaces the manual pass docs/testing.md used to ask for. It
 * walks that checklist in both themes, in real pixels where a computed
 * colour could lie (paintedInk.ts / renderedColor.ts, at SAMPLE_SCALE):
 *
 *  1. the focus ring on the topbar search, a rail link and a Lead Queue row
 *     control paints the system Highlight (twin: forced-color-adjust none +
 *     a transparent outline on the search makes the check fail);
 *  2. the active rail item, a segmented button, a filter chip, a drawer tab,
 *     the command palette's active row and the filter menu's focused and
 *     selected options paint the system pair: every text run and glyph
 *     inks HighlightText on the Highlight fill, at 4.5:1 (text) and 3:1
 *     (glyphs) unless the palette's own pair is weaker (paintedInk.ts
 *     highlightInkFaults; twin: an authored ink that clears 4.5:1 still
 *     fails);
 *  3. confidence bars, the status-pill dot, map regions, legend bars and ZIP
 *     tiles keep a visible CanvasText edge;
 *  4. score chips keep their solid / dashed / dotted borders.
 *
 * Every test first proves `matchMedia('(forced-colors: active)')` and reads
 * the system colours from a probe element. A check that fails for a CSS
 * defect is annotated `test.fixme(true, '<owner lane> · a11y-10 item 4 /
 * css-06 item 3 · <what failed>')`; w5-design-contract owns
 * 33-contrast-modes.css, tokens.css and 01-app-shell.css in W5a. Nothing is
 * skipped silently. Playwright Firefox does not start on the macOS 27 host:
 * run it on CI's Linux runners or in the pinned Playwright container.
 */
import type { Locator, Page } from '@playwright/test';
import { mapAllClassesFixture } from './data/mapEncoding';
import {
  INK_FLOOR,
  SAMPLE_SCALE,
  describeInk,
  describePair,
  highlightInkFaults,
  paintedInks,
  paintedSystemFill,
  sameColor,
  systemHighlightPair,
  type PaintedInk,
  type SystemPair,
} from './paintedInk';
import { asComputedRgb, centerPixel, contrastRatio, settleTransitions, type Rgb } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

// Layout stays 1440x900 CSS px; only the raster is finer (see paintedInk.ts).
test.use({ deviceScaleFactor: SAMPLE_SCALE });

interface SystemColors {
  highlight: Rgb;
  canvas: Rgb;
  canvasText: Rgb;
}

/** The forced palette, read from a probe element, after proving forced colors are on. */
async function forcedPalette(page: Page): Promise<SystemColors> {
  expect(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), 'precondition: forced colors are active').toBe(true);
  // Composited over the forced Canvas: a palette may give Highlight an alpha.
  const read = (keyword: string) => paintedSystemFill(page, keyword);
  const palette = { highlight: await read('Highlight'), canvas: await read('Canvas'), canvasText: await read('CanvasText') };
  expect(sameColor(palette.canvas, palette.canvasText), 'precondition: Canvas and CanvasText differ').toBe(false);
  return palette;
}

/** Focus the way a keyboard user reaches it, so :focus-visible matches. */
async function keyboardFocus(page: Page, target: Locator): Promise<void> {
  await page.keyboard.press('Tab');
  await target.focus();
  await settleTransitions(target);
  expect(await target.evaluate((el) => el.matches(':focus-visible')), 'precondition: :focus-visible').toBe(true);
}

/** The dominant painted colour of a CSS-px clip, from a device-scale screenshot. */
async function dominantColor(page: Page, clip: { x: number; y: number; width: number; height: number }): Promise<Rgb> {
  const png = await page.screenshot({ animations: 'disabled', caret: 'hide', scale: 'device', clip });
  return page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement('canvas');
    [canvas.width, canvas.height] = [image.width, image.height];
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('2d canvas unavailable');
    context.drawImage(image, 0, 0);
    const { data } = context.getImageData(0, 0, image.width, image.height);
    const counts = new Map<number, number>();
    for (let i = 0; i < data.length; i += 4) {
      const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const key = [...counts].sort((a, b) => b[1] - a[1])[0][0];
    return [(key >> 16) & 255, (key >> 8) & 255, key & 255] as [number, number, number];
  }, png.toString('base64'));
}

/** The painted colour of the focus ring: the outline band left of the box, middle half of its height. */
async function ringPixel(page: Page, target: Locator): Promise<Rgb> {
  const band = await target.evaluate((el) => {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    const width = Number.parseFloat(style.outlineWidth) || 0;
    const offset = Number.parseFloat(style.outlineOffset) || 0;
    return { x: box.left - offset - width, width, y: box.top + box.height / 4, height: box.height / 2 };
  });
  expect(band.width, 'precondition: a focus ring is drawn').toBeGreaterThan(0);
  return dominantColor(page, { x: band.x, y: band.y, width: band.width, height: band.height });
}

/**
 * The system Highlight pair, proved usable, and recorded on the test.
 *
 * Why the oracle is the PAIR, not a bare 4.5:1 (W5b CI, run 37521773357,
 * PR #267): the light run failed every sample of every state with ink
 * rgb(255, 255, 255) on rgb(51, 153, 255) at 2.94:1. Its trace shows the
 * probe resolving Highlight to rgb(51, 153, 255), Canvas to white and
 * CanvasText to black, and the served CSS giving each state
 * forced-color-adjust: none and color: HighlightText on itself and every
 * descendant (33-contrast-modes.css, W5a ruling R2). #3399ff / #ffffff is
 * Firefox's own light standin pair (nsXPLookAndFeel's
 * GetStandinForNativeColor, which headless Linux Firefox uses), whose WCAG
 * ratio is 2.94:1. So a fixed 4.5:1 measured the palette CI's Firefox ships,
 * not our CSS, and no CSS that honours the user's palette could pass it.
 * The product owns painting the system pair exactly; the palette owns its
 * contrast (see highlightInkFaults).
 */
async function highlightPair(page: Page): Promise<SystemPair> {
  const pair = await systemHighlightPair(page);
  expect(sameColor(pair.ink, pair.ground), `precondition: HighlightText and Highlight differ (${describePair(pair)})`).toBe(false);
  test.info().annotations.push({ type: 'forced palette', description: describePair(pair) });
  return pair;
}

/** Every sample paints the system pair (highlightInkFaults); the state must yield the kinds it holds. */
async function expectReadable(page: Page, state: string, target: Locator, expects: { text: boolean; glyph: boolean; pair: SystemPair }): Promise<PaintedInk[]> {
  const inks = await paintedInks(page, target, expects.pair.ground);
  const listing = inks.map((ink) => describeInk(state, ink)).join('\n');
  if (expects.text) expect(inks.some((ink) => ink.kind === 'text'), `${state}: a text run is sampled\n${listing}`).toBe(true);
  if (expects.glyph) expect(inks.some((ink) => ink.kind === 'glyph'), `${state}: a glyph is sampled\n${listing}`).toBe(true);
  // Soft, so one run reports every state that fails, not only the first.
  for (const ink of inks) {
    expect.soft(highlightInkFaults(ink, expects.pair), `${describeInk(state, ink)} (system ${describePair(expects.pair)})`).toEqual([]);
  }
  return inks;
}

/**
 * Inks a non-vacuity twin may author: the first that is NOT the system
 * HighlightText yet clears 4.5:1 on Highlight with room to spare, so only
 * the pairing check can fail it (black for CI's #3399ff, yellow on a dark
 * Highlight such as Windows HC White's #37006e).
 */
const AUTHORED_INKS: readonly Rgb[] = [
  [0, 0, 0],
  [255, 255, 255],
  [0, 0, 128],
  [255, 255, 0],
];

/** A detached class probe (no drawer or borrower read opens): a state label with a glyph. */
async function tabProbe(page: Page, className: string): Promise<Locator> {
  await page.locator('#main-content').evaluate((main, cls) => {
    const host = document.createElement('div');
    host.dataset.forcedFirefoxProbe = cls;
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = cls;
    tab.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10"/></svg>';
    tab.append(' Evidence');
    host.appendChild(tab);
    main.prepend(host);
  }, className);
  return page.locator(`[data-forced-firefox-probe="${className}"] > button`);
}

async function edge(target: Locator, property: 'border' | 'stroke'): Promise<{ color: string; style: string }> {
  return target.evaluate((el, prop) => {
    const style = getComputedStyle(el);
    return prop === 'stroke'
      ? { color: style.stroke, style: style.strokeWidth === '0px' ? 'none' : 'solid' }
      : { color: style.borderTopColor, style: style.borderTopStyle };
  }, property);
}

test.describe('Firefox forced colors (a11y-10 item 4 / css-06 item 3)', () => {
  for (const theme of THEMES) {
    test.describe(theme, () => {
      test.beforeEach(async ({ app, browserName, page }) => {
        await app.setTheme(theme);
        // Firefox forces colors from its launch pref; a Chromium sanity run emulates it.
        if (browserName === 'chromium') await page.emulateMedia({ forcedColors: 'active' });
      });

      test('the focus ring paints the system Highlight on the topbar search, a rail link and a row control', async ({ app, page }) => {
        await app.gotoRoute('/lead-queue');
        const { highlight } = await forcedPalette(page);
        const targets: Record<string, Locator> = {
          'topbar search': page.locator('.topbar__search input'),
          'rail link': page.locator('.rail__item').first(),
          'Lead Queue row checkbox': page.locator('table.tbl tbody [data-testid^="lead-select-"]').first(),
        };
        for (const [name, target] of Object.entries(targets)) {
          await keyboardFocus(page, target);
          const ring = await ringPixel(page, target);
          expect.soft(sameColor(ring, highlight, 3), `${name}: ring rgb(${ring.join(', ')}) is Highlight rgb(${highlight.join(', ')})`).toBe(true);
        }
      });

      test('non-vacuity: with forced-color-adjust none and a transparent outline, the search ring is not Highlight', async ({ app, page }) => {
        await app.gotoRoute('/lead-queue');
        const { highlight } = await forcedPalette(page);
        await page.addStyleTag({
          content: '.topbar__search input:focus-visible { forced-color-adjust: none !important; outline-color: transparent !important; }',
        });
        const search = page.locator('.topbar__search input');
        await keyboardFocus(page, search);
        expect(sameColor(await ringPixel(page, search), highlight, 3), 'the check can fail').toBe(false);
      });

      test('Highlight states fill with readable text and glyphs', async ({ app, page }) => {
        // First CI run (W5a, 2026-10-01): Firefox paints no Canvas backplate
        // behind forced text, so these states drew their forced ink straight
        // on Highlight (1.18:1 dark; LinkText at 3.20:1 light). Since W5b
        // (w5-theme-white-label) they opt out of forcing and paint
        // HighlightText on Highlight themselves (33-contrast-modes.css), so
        // the check is that they paint the system pair (highlightPair).
        await app.gotoRoute('/lead-queue?state=IL');
        await forcedPalette(page);
        const pair = await highlightPair(page);
        const both = { text: true, glyph: true, pair };

        await expectReadable(page, 'active rail item', page.locator('.rail__item.is-active'), both);
        const statePill = page.getByRole('combobox', { name: 'STATE: IL' });
        await expect(statePill).toHaveClass(/\bis-active\b/);
        await expectReadable(page, 'STATE filter chip', statePill, both);
        await expectReadable(page, 'drawer tab', await tabProbe(page, 'drawer__tab is-active'), both);

        await statePill.focus();
        await page.keyboard.press('ArrowDown');
        const menu = page.getByRole('listbox', { name: 'STATE' });
        await expect(menu).toBeVisible();
        await page.keyboard.press('ArrowDown');
        const cursor = menu.locator('.filter-menu__item.is-focused');
        const selected = menu.locator('.filter-menu__item.is-selected:not(.is-focused)');
        await expect(cursor).toHaveCount(1);
        await expect(selected).toHaveCount(1);
        await expectReadable(page, 'filter-menu focused option', cursor, { ...both, glyph: false });
        await expectReadable(page, 'filter-menu selected option', selected, { ...both, glyph: false });
        await page.keyboard.press('Escape');
        await expect(menu).toHaveCount(0);

        const consolePanel = await app.openConsole();
        const pressed = consolePanel.getByRole('group', { name: 'Density' }).locator('button[aria-pressed="true"]');
        await expectReadable(page, 'segmented button (Console density)', pressed, { ...both, glyph: false });

        const palette = await app.openCommandPalette();
        await page.keyboard.press('ArrowDown');
        const row = palette.locator('.cmdk__row.is-active');
        await expect(row).toHaveCount(1);
        await expectReadable(page, 'command palette active row', row, both);
      });

      test('non-vacuity: a Highlight state inked with an authored colour fails, though it clears 4.5:1', async ({ app, page }) => {
        await app.gotoRoute('/lead-queue');
        await forcedPalette(page);
        const pair = await highlightPair(page);
        const authored = AUTHORED_INKS.find((rgb) => !sameColor(rgb, pair.ink, 32) && contrastRatio(rgb, pair.ground) >= INK_FLOOR.text + 1);
        expect(authored, `precondition: an authored ink that is not HighlightText clears 5.5:1 on Highlight (${describePair(pair)})`).toBeDefined();
        const ink = `rgb(${(authored as Rgb).join(', ')})`;
        const tab = await tabProbe(page, 'drawer__tab is-active');
        // Out-ranks R2's `.is-active *:not(svg *)` (0,2,2): forcing stays
        // off, but the state and its svg ink an authored colour.
        await page.addStyleTag({
          content: `[data-forced-firefox-probe] > button.drawer__tab.is-active, [data-forced-firefox-probe] > button.drawer__tab.is-active * { forced-color-adjust: none !important; color: ${ink} !important; }`,
        });
        const inks = await paintedInks(page, tab, pair.ground);
        const listing = inks.map((sample) => describeInk('authored drawer tab', sample)).join('\n');
        expect(inks.map((sample) => sample.kind).sort(), `both kinds are sampled\n${listing}`).toEqual(['glyph', 'text']);
        for (const sample of inks) {
          const line = describeInk('authored drawer tab', sample);
          // A bare WCAG floor passes this ink: it is readable, just not the user's palette.
          expect(sample.ratio, `${line}: the bare floor alone would pass`).toBeGreaterThanOrEqual(INK_FLOOR[sample.kind]);
          expect(highlightInkFaults(sample, pair), `${line}: the pairing check fails`).toContainEqual(expect.stringMatching(/^ink is not the system HighlightText/));
        }
      });

      test('data marks keep a visible CanvasText edge', async ({ app, mockApi, page }) => {
        mockApi.register(mapAllClassesFixture.method, mapAllClassesFixture.pattern, mapAllClassesFixture.handler);
        await app.gotoRoute('/lead-queue');
        const { canvasText } = await forcedPalette(page);
        const ink = await asComputedRgb(page, 'CanvasText');

        const lit = page.locator('#main-content .conf__bar.on').first();
        const unlit = page.locator('#main-content .conf__bar:not(.on)').first();
        await expect(lit).toBeVisible();
        expect(await edge(lit, 'border'), 'a lit confidence bar').toEqual({ color: ink, style: 'solid' });
        expect((await edge(unlit, 'border')).style, 'an unlit bar differs in edge style').toBe('dashed');

        const dot = page.getByTestId('system-status-pill').locator('.dot');
        await expect(dot).toBeVisible();
        expect(await dot.evaluate((el) => getComputedStyle(el).backgroundColor), 'the status-pill dot').toBe(ink);
        expect(sameColor(await centerPixel(page, dot), canvasText, 3), 'the dot paints CanvasText').toBe(true);

        await app.gotoRoute('/');
        const region = page.locator('#main-content path.map-region.has-data').first();
        await expect(region).toBeVisible();
        expect((await edge(region, 'stroke')).color, 'a map region').toBe(ink);
        const legendBar = page.locator('.map-legend__bar span').first();
        await expect(legendBar).toBeVisible();
        expect(await edge(legendBar, 'border'), 'a legend bar').toEqual({ color: ink, style: 'solid' });

        await app.gotoRoute('/?geo_state=IL');
        const tile = page.locator('button.zip-tile').first();
        await expect(tile).toBeVisible();
        expect(await edge(tile, 'border'), 'a ZIP tile').toEqual({ color: ink, style: 'solid' });
      });

      test('score chips keep their solid, dashed and dotted borders', async ({ app, page }) => {
        await app.gotoRoute('/lead-queue');
        await forcedPalette(page);
        const styles = await page.locator('#main-content').evaluate((main) => {
          const host = document.createElement('div');
          main.prepend(host);
          return ['high', 'med', 'low'].map((band) => {
            const chip = document.createElement('span');
            chip.className = `score score--${band}`;
            chip.textContent = '72';
            host.appendChild(chip);
            return getComputedStyle(chip).borderTopStyle;
          });
        });
        expect(styles).toEqual(['solid', 'dashed', 'dotted']);
      });
    });
  }
});
