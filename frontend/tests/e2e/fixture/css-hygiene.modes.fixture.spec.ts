/**
 * Rendered-layer proofs for the OS contrast modes (2026-09-21 audit css-06,
 * a11y-10, responsive-v3): forced colors (Windows High Contrast), more
 * contrast, and reduced transparency, in both themes. Source contracts:
 * src/design-system/contrastModes.css.test.ts. The real-pixel legibility of
 * text and glyphs inside forced Highlight states is
 * css-hygiene.forced-ink.fixture.spec.ts.
 */
import type { Locator, Page } from '@playwright/test';
import { mapAllClassesFixture } from './data/mapEncoding';
import { asComputedRgb, contrastRatio, parseRgb, renderedColors, settleTransitions, tokenValue } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

/** Focus an element the way a keyboard user reaches it, so :focus-visible matches. */
async function keyboardFocus(page: Page, target: Locator): Promise<void> {
  await page.keyboard.press('Tab');
  await target.focus();
  await settleTransitions(target);
}

async function outline(target: Locator): Promise<{ visible: boolean; style: string; color: string }> {
  return target.evaluate((el) => ({
    visible: el.matches(':focus-visible'),
    style: getComputedStyle(el).outlineStyle,
    color: getComputedStyle(el).outlineColor,
  }));
}

/**
 * An element's computed fill and ink, composited: enough to tell a forced
 * state's fill from its idle one. Computed colours cannot see Chromium's
 * text backplate, so text legibility inside a state is proven in real
 * pixels by css-hygiene.forced-ink.fixture.spec.ts, never here.
 */
async function painted(target: Locator): Promise<string> {
  const colors = await renderedColors(target);
  return `bg rgb(${colors.bg.join(', ')}) / fg rgb(${colors.fg.join(', ')})`;
}

test.describe('forced colors (css-06 / a11y-10 / responsive-v3)', () => {
  for (const theme of THEMES) {
    test.describe(theme, () => {
      test.beforeEach(async ({ app, page }) => {
        await app.setTheme(theme);
        await page.emulateMedia({ forcedColors: 'active' });
      });

      test('the palette input, a rail link and a text field ring in the system Highlight', async ({ app, page }) => {
        await app.gotoRoute('/');
        expect(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), 'precondition: forced colors').toBe(true);
        const highlight = await asComputedRgb(page, 'Highlight');

        const rail = page.locator('.rail__item').first();
        await keyboardFocus(page, rail);
        expect(await outline(rail)).toEqual({ visible: true, style: 'solid', color: highlight });

        const consolePanel = await app.openConsole();
        const field = consolePanel.locator('.form-input').first();
        await keyboardFocus(page, field);
        expect(await outline(field)).toEqual({ visible: true, style: 'solid', color: highlight });

        const palette = await app.openCommandPalette();
        const input = palette.locator('.cmdk__input');
        await expect(input).toBeFocused();
        await settleTransitions(input);
        expect(await outline(input)).toEqual({ visible: true, style: 'solid', color: highlight });
      });

      test('active and inactive states stay distinct once fills are forced', async ({ app, page }) => {
        await app.gotoRoute('/lead-queue');
        const highlight = await asComputedRgb(page, 'Highlight');

        // The route nav keeps its state in the underline (visual-05): the
        // current link's indicator is Highlight, an idle link's is Canvas
        // (a forced transparent border would line every link).
        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        const activeLink = nav.locator('.route-nav__link[aria-current="page"]');
        const idleLink = nav.locator('.route-nav__link:not([aria-current])').first();
        const indicator = (link: Locator) => link.evaluate((el) => {
          const style = getComputedStyle(el);
          return `${style.borderBottomWidth} ${style.borderBottomStyle} ${style.borderBottomColor}`;
        });
        expect(await indicator(activeLink)).toBe(`2px solid ${highlight}`);
        expect(await indicator(idleLink)).toBe(`2px solid ${await asComputedRgb(page, 'Canvas')}`);
        expect(await indicator(activeLink)).not.toBe(await indicator(idleLink));

        const bars = page.locator('#main-content .conf__bar');
        await expect(bars.first()).toBeVisible();
        const on = await page.locator('#main-content .conf__bar.on').first().evaluate((el) => getComputedStyle(el).backgroundColor);
        const off = await page.locator('#main-content .conf__bar:not(.on)').first().evaluate((el) => getComputedStyle(el).backgroundColor);
        expect(on, 'a lit confidence bar').toBe(await asComputedRgb(page, 'CanvasText'));
        expect(off, 'an unlit confidence bar').toBe(await asComputedRgb(page, 'Canvas'));

        const consolePanel = await app.openConsole();
        const density = consolePanel.getByRole('group', { name: 'Density' });
        const pressed = density.locator('button[aria-pressed="true"]');
        const unpressed = density.locator('button[aria-pressed="false"]');
        expect(await painted(pressed)).not.toBe(await painted(unpressed));
        expect(await pressed.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(highlight);

        const toggle = consolePanel.getByRole('button', { name: 'Toggle signal strength meters' });
        const state = async () => ({
          track: await painted(toggle),
          knob: await toggle.evaluate((el) => getComputedStyle(el, '::after').backgroundColor),
        });
        const first = await state();
        await toggle.click();
        await settleTransitions(toggle);
        const second = await state();
        expect(second.track, 'switch track on vs off').not.toBe(first.track);
        expect(second.knob, 'switch knob on vs off').not.toBe(first.knob);
        expect([first.knob, second.knob].sort()).toEqual(
          [await asComputedRgb(page, 'CanvasText'), await asComputedRgb(page, 'HighlightText')].sort(),
        );
      });

      test('map steps keep distinct fills edged in CanvasText, and a focused Sankey node strokes Highlight', async ({ app, mockApi, page }) => {
        // The map-encoding rollup that paints every class (data/mapEncoding.ts).
        mockApi.register(mapAllClassesFixture.method, mapAllClassesFixture.pattern, mapAllClassesFixture.handler);
        await app.gotoRoute('/');
        await expect(page.locator('path.map-region.has-data').first()).toBeVisible();
        await expect(page.locator('.map-levels')).toHaveAttribute('aria-busy', 'false');
        const canvasText = await asComputedRgb(page, 'CanvasText');
        const regions = await page.locator('#main-content path.map-region[data-map-class]').evaluateAll((paths) =>
          paths
            .map((path) => {
              const level = `lvl-${path.getAttribute('data-map-class')}`;
              const style = getComputedStyle(path);
              return /^lvl-[1-4]$/.test(level) ? { level, fill: style.fill, stroke: style.stroke } : null;
            })
            .filter((region): region is { level: string; fill: string; stroke: string } => region !== null),
        );
        const fillByLevel = new Map(regions.map((region) => [region.level, region.fill]));
        expect([...fillByLevel.keys()].sort(), 'precondition: all four steps on the map').toEqual(['lvl-1', 'lvl-2', 'lvl-3', 'lvl-4']);
        expect(new Set(fillByLevel.values()).size, 'four pairwise-distinct fills').toBe(4);
        for (const region of regions) expect(region.stroke, region.level).toBe(canvasText);

        await app.gotoRoute('/analytics');
        const node = page.locator('#main-content .funnel-sankey__node').first();
        await keyboardFocus(page, node);
        const bar = node.locator('.funnel-sankey__bar');
        await settleTransitions(bar);
        expect(await bar.evaluate((el) => getComputedStyle(el).stroke)).toBe(await asComputedRgb(page, 'Highlight'));
      });

      test('the score band survives as a border style', async ({ app, page }) => {
        await app.gotoRoute('/lead-queue');
        // Probes: the fixture queue need not hold all three bands at once.
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

/** The prefers-contrast: more values tokens.css declares, per theme. */
const MORE_CONTRAST: Record<FixtureTheme, Record<string, string>> = {
  dark: {
    '--text-3': '#BACBD8',
    '--text-4': '#93AABD',
    '--line-1': 'rgba(178,189,194,0.30)',
    '--line-2': 'rgba(178,189,194,0.60)',
    '--line-3': 'rgba(102,197,255,0.60)',
    '--focus-ring-width': '3px',
  },
  light: {
    '--text-3': '#254A66',
    '--text-4': '#34536B',
    '--line-1': 'rgba(2,80,128,0.30)',
    '--line-2': 'rgba(2,80,128,0.60)',
    '--line-3': 'rgba(2,80,128,0.72)',
    '--focus-ring-width': '3px',
  },
};

test.describe('prefers-contrast: more (css-06 / a11y-10)', () => {
  for (const theme of THEMES) {
    test(`${theme}: the tertiary inks, hairlines and ring width step up, and tertiary text clears 7:1`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      const root = page.locator('html');
      // Colours compare as computed rgb(): the build minifies token text.
      const normalize = async (name: string, value: string) => (name.startsWith('--focus') ? value : asComputedRgb(page, value));
      const read = async () =>
        Object.fromEntries(
          await Promise.all(
            Object.keys(MORE_CONTRAST[theme]).map(async (name) => [name, await normalize(name, await tokenValue(root, name))] as const),
          ),
        );
      const expected = Object.fromEntries(
        await Promise.all(Object.entries(MORE_CONTRAST[theme]).map(async ([name, value]) => [name, await normalize(name, value)] as const)),
      );
      const defaults = await read();
      await page.emulateMedia({ contrast: 'more' });
      expect(await page.evaluate(() => matchMedia('(prefers-contrast: more)').matches)).toBe(true);
      const boosted = await read();
      expect(boosted).toEqual(expected);
      for (const name of Object.keys(boosted)) expect(boosted[name], `${name} differs from its default`).not.toBe(defaults[name]);

      const label = page.locator('.kpi__label').first();
      const colors = await renderedColors(label);
      expect(colors.color).toBe(await asComputedRgb(page, MORE_CONTRAST[theme]['--text-3']));
      const ratio = contrastRatio(colors.fg, colors.bg);
      expect(ratio, `--text-3 label ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(7);
      expect(contrastRatio(parseRgb(await asComputedRgb(page, MORE_CONTRAST[theme]['--text-3'])), colors.bg)).toBeGreaterThanOrEqual(7);
    });
  }
});

test.describe('prefers-reduced-transparency: reduce (responsive-v3)', () => {
  for (const theme of THEMES) {
    test(`${theme}: frosted bars turn opaque and every blur is dropped`, async ({ app, browserName, page }) => {
      test.skip(browserName !== 'chromium', 'emulated through a Chromium DevTools Protocol media feature');
      await app.setTheme(theme);
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Emulation.setEmulatedMedia', {
        features: [
          { name: 'prefers-reduced-transparency', value: 'reduce' },
          { name: 'prefers-color-scheme', value: theme },
          { name: 'prefers-reduced-motion', value: 'reduce' },
        ],
      });
      await app.gotoRoute('/');
      expect(await page.evaluate(() => matchMedia('(prefers-reduced-transparency: reduce)').matches), 'precondition').toBe(true);
      const read = (selector: string) =>
        page.locator(selector).first().evaluate((el) => ({ blur: getComputedStyle(el).backdropFilter, background: getComputedStyle(el).backgroundColor }));
      for (const selector of ['.topbar', '.route-nav', '.map-legend']) {
        const surface = await read(selector);
        expect(surface.blur, selector).toBe('none');
        expect(surface.background, `${selector} is opaque`).toMatch(/^rgb\(/);
      }
      const palette = await app.openCommandPalette();
      await expect(palette).toBeVisible();
      expect((await read('.cmdk')).blur).toBe('none');
    });
  }
});
