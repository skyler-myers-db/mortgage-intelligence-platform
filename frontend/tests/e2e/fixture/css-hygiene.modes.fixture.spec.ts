/**
 * Rendered-layer proofs for the OS contrast modes (2026-09-21 audit css-06,
 * a11y-10, responsive-v3): forced colors (Windows High Contrast), more
 * contrast, and reduced transparency, in both themes. Source contracts:
 * src/design-system/contrastModes.css.test.ts.
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

/** What a user sees behind and on an element: the composited fill and ink. */
async function painted(target: Locator): Promise<string> {
  const colors = await renderedColors(target);
  return `bg rgb(${colors.bg.join(', ')}) / fg rgb(${colors.fg.join(', ')})`;
}

interface Ink {
  what: string;
  kind: 'text' | 'glyph';
  ratio: number;
}

/**
 * Every visible label and svg glyph inside one element (the element too),
 * with the contrast of its PAINTED ink against the fill composited behind
 * it: its own background, then each ancestor's, out to the first opaque
 * layer. A label is an element with its own non-blank text; a glyph is an
 * svg shape, inked by its stroke, or by its fill when it has no stroke.
 * Waits out every transition in the subtree first (the reduced-motion reset
 * gives each element a 0.01ms `all` transition).
 */
async function subtreeInks(target: Locator): Promise<Ink[]> {
  return target.evaluate(async (root) => {
    await Promise.all(
      root
        .getAnimations({ subtree: true })
        .filter((animation) => animation instanceof CSSTransition)
        .map((transition) => transition.finished.catch(() => undefined)),
    );
    type Rgba = [number, number, number, number];
    type Rgb = [number, number, number];
    const parse = (value: string): Rgba => {
      const match = /^rgba?\(([^)]+)\)$/.exec(value.trim());
      if (!match) throw new Error(`unparseable colour: ${value}`);
      const [r, g, b, a = '1'] = match[1].split(/[\s,/]+/).filter(Boolean);
      return [Number(r), Number(g), Number(b), Number(a)];
    };
    const over = (top: Rgba, under: Rgb): Rgb => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])) as Rgb;
    const backdrop = (el: Element): Rgb => {
      const layers: Rgba[] = [];
      for (let node: Element | null = el; node; node = node.parentElement) {
        const layer = parse(getComputedStyle(node).backgroundColor);
        if (layer[3] > 0) layers.push(layer);
        if (layer[3] >= 1) break;
      }
      const base = layers.pop();
      if (!base || base[3] < 1) throw new Error(`no opaque fill behind ${el.tagName}`);
      return layers.reduceRight<Rgb>((under, layer) => over(layer, under), [base[0], base[1], base[2]]);
    };
    const WEIGHTS = [0.2126, 0.7152, 0.0722];
    const luminance = (rgb: Rgb) =>
      rgb.reduce((sum, v, i) => {
        const c = v / 255;
        return sum + WEIGHTS[i] * (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
      }, 0);
    const ratio = (a: Rgb, b: Rgb) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };
    const inks: Array<{ what: string; kind: 'text' | 'glyph'; ratio: number }> = [];
    for (const el of [root, ...root.querySelectorAll('*')]) {
      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (box.width <= 1 || box.height <= 1 || style.visibility !== 'visible') continue;
      const glyph = el instanceof SVGGeometryElement;
      const text = [...el.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && Boolean(node.textContent?.trim()));
      if (!glyph && !text) continue;
      const ink = glyph ? (style.stroke !== 'none' ? style.stroke : style.fill) : style.color;
      const fill = backdrop(el);
      const tag = el.tagName.toLowerCase();
      // A glyph is named by its nearest classed ancestor (the icon's svg or its host).
      const owner = el.closest('[class]:not([class=""])')?.getAttribute('class') ?? '';
      inks.push({
        what: glyph ? `${owner} ${tag}` : `${el.getAttribute('class') || tag} "${el.textContent?.trim()}"`,
        kind: glyph ? 'glyph' : 'text',
        ratio: ratio(over(parse(ink), fill), fill),
      });
    }
    return inks;
  });
}

/** Text clears 4.5:1 and a glyph 3:1 (WCAG 1.4.3 / 1.4.11) on the forced fill behind it. */
function expectLegible(state: string, inks: readonly Ink[]): void {
  for (const ink of inks) {
    const floor = ink.kind === 'text' ? 4.5 : 3;
    expect(ink.ratio, `${state}: ${ink.kind} ${ink.what} at ${ink.ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(floor);
  }
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

        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        const activeLink = nav.locator('.filter.is-active');
        const idleLink = nav.locator('.filter:not(.is-active)').first();
        expect(await painted(activeLink)).not.toBe(await painted(idleLink));
        expect(await activeLink.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(highlight);

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

      // Chromium forces a child's OWN ink to its element's system colour
      // (LinkText inside the route-nav link) and keeps an svg's authored ink,
      // so before the subtree rule in 33-contrast-modes.css the current page's
      // route-nav label and the palette row's hint and icon sat on the
      // Highlight fill at 1.2-2.3:1. The container's own colour never showed it.
      test('every label and glyph inside a Highlight state stays legible on the fill', async ({ app, page }) => {
        // State applied by URL: the State pill goes active, and an Owner Link
        // hero chip (with its remove glyph) and the More filters toggle join it.
        await app.gotoRoute(`/lead-queue?state=IL&owner_link=${encodeURIComponent('Portfolio investor (5+)')}`);
        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        const current = await subtreeInks(nav.locator('.filter.is-active'));
        expect(current.map((ink) => ink.what)).toContain('filter__value "Leads"');
        expect(current.some((ink) => ink.kind === 'glyph'), 'the route glyph is measured').toBe(true);
        expectLegible('current-page route-nav link', current);

        const applied: Record<string, Locator> = {
          'STATE pill': page.getByRole('combobox', { name: 'STATE: IL' }),
          'More filters toggle': page.getByTestId('lead-queue-more-filters'),
          'OWNER LINK hero chip': page.getByTestId('lead-queue-active-filters').locator('.filter.is-active'),
        };
        for (const [name, chip] of Object.entries(applied)) {
          await expect(chip, name).toHaveClass(/\bis-active\b/);
          const inks = await subtreeInks(chip);
          expect(inks.some((ink) => ink.kind === 'text'), `${name} labels are measured`).toBe(true);
          expect(inks.some((ink) => ink.kind === 'glyph'), `${name} glyph is measured`).toBe(true);
          expectLegible(name, inks);
        }
        // Every other applied-filter chip on the page (the drilldown's State chip).
        for (const chip of await page.locator('#main-content .filter.is-active:not(.route-nav .filter)').all()) {
          expectLegible('applied filter chip', await subtreeInks(chip));
        }

        const palette = await app.openCommandPalette();
        await page.keyboard.press('ArrowDown');
        const row = palette.locator('.cmdk__row.is-active');
        await expect(row).toHaveCount(1);
        const rowInks = await subtreeInks(row);
        const kinds = rowInks.map((ink) => `${ink.kind}:${ink.what.split(' ')[0]}`);
        expect(kinds).toEqual(expect.arrayContaining(['text:cmdk__row-label', 'text:cmdk__row-hint', 'glyph:cmdk__row-icon']));
        expectLegible('active palette row', rowInks);
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
