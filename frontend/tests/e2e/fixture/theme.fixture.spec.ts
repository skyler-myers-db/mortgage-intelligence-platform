/**
 * Theming correctness, proven at the rendered layer (2026-09-21 audit
 * visual-03, css-02, responsive-03, css-01, css-v1, a11y-01, responsive-02).
 *
 *  a. a stored light theme is on the FIRST painted document, before React
 *     mounts (public/theme-boot.js, render-blocking in <head>), and so is a
 *     Console the presenter left open (no .main gutter shift after mount);
 *  b. `color-scheme` follows the theme, so a native checkbox in the Lead
 *     Queue paints dark in the dark theme (pixel-sampled, not just computed);
 *  c. <meta name="theme-color"> follows the theme;
 *  d. nothing stored + `prefers-color-scheme: light` boots light, and the
 *     Console's System option follows OS flips live, theme-color included;
 *     the Administration appearance section marks and edits the same
 *     preference (it used to mark the painted theme, so System read "Light");
 *  e. every theme x accent pair paints the shared token focus ring at 3:1,
 *     from the global :focus-visible rule and from a bespoke one, the skip
 *     link and the primary CTA (at rest and hovered) at 4.5:1 (a11y-01,
 *     --accent-fill), and passes the shared axe gate (fixture/axe.ts) on /
 *     and /lead-queue with no ratchet entry;
 *  f. every theme x accent pair prints the accent family monochrome, as
 *     print.css asks (the (0,2,0) theme x accent compounds used to outrank
 *     its remap: dark + navy printed accent-ink #66C5FF on white paper);
 *  g. data ink is fixed per theme (D-dataviz-geo-c1): an accent switch
 *     repaints the primary button but never a sparkline or the choropleth.
 *
 * States the axe loop never renders (warning copy, amber glyphs, the active
 * evidence-drawer tab, text-input focus) are proven in
 * contrastStates.fixture.spec.ts.
 */
import type { Locator, Page } from '@playwright/test';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { mapAllClassesFixture } from './data/mapEncoding';
import {
  asComputedRgb,
  centerPixel,
  contrastRatio,
  parseRgb,
  relativeLuminance,
  renderedColors,
  settleTransitions,
} from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const ACCENTS = ['bright', 'teal', 'navy', 'red'] as const;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const TRANSPARENT = 'rgba(0, 0, 0, 0)';
/** CanvasText / Canvas under the light color-scheme tokens.css forces for print. */
const PRINT_INK = 'rgb(0, 0, 0)';
const PRINT_PAPER = 'rgb(255, 255, 255)';
/** The fixed data ink per theme (tokens.css --accent-data; D-dataviz-geo-c1). */
const DATA_INK: Record<FixtureTheme, string> = { dark: '#66C5FF', light: '#014E80' };

interface AttributeTrace {
  atDomContentLoaded: string | null;
  transitions: string[];
}

declare global {
  interface Window {
    __mipAttrTrace?: Record<string, AttributeTrace>;
  }
}

/** Seed one localStorage key before the next document, the way app.setTheme does. */
async function seedStorage(page: Page, key: string, value: string): Promise<void> {
  await page.addInitScript(
    ([k, v]) => {
      try {
        window.localStorage.setItem(k, v);
      } catch {
        // about:blank has no storage; the next document seeds it.
      }
    },
    [key, value] as const,
  );
}

/** Record one <html> attribute at DOMContentLoaded and every transition it goes through. */
async function traceRootAttribute(page: Page, attribute: string): Promise<void> {
  await page.addInitScript((name) => {
    const trace: AttributeTrace = { atDomContentLoaded: null, transitions: [] };
    (window.__mipAttrTrace ??= {})[name] = trace;
    new MutationObserver(() => {
      trace.transitions.push(document.documentElement.getAttribute(name) ?? 'none');
    }).observe(document, { attributes: true, subtree: true, attributeFilter: [name] });
    document.addEventListener('DOMContentLoaded', () => {
      trace.atDomContentLoaded = document.documentElement.getAttribute(name);
    });
  }, attribute);
}

/**
 * `<meta name="theme-color">` and the painted `--bg-0`, both as computed
 * rgb() so hex spellings meet. An absent or empty meta reads as '' (a probe
 * would otherwise inherit the body colour and look like a real value).
 */
async function themeColorAndPageBackground(page: Page): Promise<{ meta: string; background: string }> {
  const [meta, background] = await page.evaluate(() => [
    document.querySelector('meta[name="theme-color"]')?.getAttribute('content')?.trim() ?? '',
    getComputedStyle(document.documentElement).getPropertyValue('--bg-0').trim(),
  ]);
  return {
    meta: meta ? await asComputedRgb(page, meta) : '',
    background: await asComputedRgb(page, background),
  };
}

/**
 * Focus `target` (after a keyboard Tab) and assert its ring: matches
 * :focus-visible, 2px solid, colour equal to the resolved
 * `--focus-ring-color` (compared through a probe so hex and rgb() spellings
 * meet), and WCAG 1.4.11 3:1 against the surface it is drawn on. The ring
 * sits outside the element (outline-offset), so that surface is the parent's.
 */
async function expectTokenRing(page: Page, target: Locator, label: string): Promise<void> {
  await target.focus();
  // The global reduced-motion reset gives every element an `all 0.01ms`
  // transition, so the ring animates in from the unfocused outline (width
  // `medium`, i.e. 3px): read it only once it has settled.
  await settleTransitions(target);
  const ring = await target.evaluate((el) => {
    const style = getComputedStyle(el);
    return {
      matchesFocusVisible: el.matches(':focus-visible'),
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      outlineColor: style.outlineColor,
      token: style.getPropertyValue('--focus-ring-color').trim(),
    };
  });
  expect(ring.matchesFocusVisible, `${label} matches :focus-visible`).toBe(true);
  expect(ring.outlineStyle, label).toBe('solid');
  expect(ring.outlineWidth, label).toBe('2px');
  expect(ring.outlineColor, label).not.toBe(TRANSPARENT);
  expect(ring.outlineColor, `${label} paints the resolved --focus-ring-color`).toBe(await asComputedRgb(page, ring.token));
  const surface = await renderedColors(target.locator('xpath=..'));
  const ratio = contrastRatio(parseRgb(ring.outlineColor), surface.bg);
  expect(ratio, `${label}: ${ring.outlineColor} on rgb(${surface.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
}

test('a stored light theme is painted before React mounts, even when the OS prefers dark', async ({ app, page }) => {
  await app.setTheme('light');
  // The OS says dark, so only the pre-paint bootstrap can make the first
  // document light; React's post-mount effect is too late for first paint.
  await page.emulateMedia({ colorScheme: 'dark' });
  await traceRootAttribute(page, 'data-theme');
  await app.gotoRoute('/');

  const trace = await page.evaluate(() => window.__mipAttrTrace?.['data-theme']);
  expect(trace?.atDomContentLoaded, 'data-theme must already be set when the parser finishes').toBe('light');
  expect(trace?.transitions, 'the document never passes through the dark default').not.toContain('dark');
  expect(trace?.transitions.length).toBeGreaterThan(0);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('a Console left open is on the first painted document, so .main never shifts after mount', async ({ app, page }) => {
  // React reflects `data-console` in a post-mount effect; without the
  // bootstrap the first frame paints .main without its Console gutter and
  // the whole page shifts 324px once the effect runs.
  await seedStorage(page, 'mip.consoleOpen', 'true');
  await traceRootAttribute(page, 'data-console');
  await app.gotoRoute('/');

  const trace = await page.evaluate(() => window.__mipAttrTrace?.['data-console']);
  expect(trace?.atDomContentLoaded, 'data-console must already be set when the parser finishes').toBe('open');
  expect(trace?.transitions, 'the document never passes through the closed default').not.toContain('closed');
  await expect(page.locator('html')).toHaveAttribute('data-console', 'open');
  await expect(page.getByRole('complementary', { name: 'Workspace console' }).locator('.tweaks__body')).toBeVisible();
  const gutter = await page.locator('.main').evaluate((el) => parseFloat(getComputedStyle(el).paddingRight));
  expect(gutter, 'the Console gutter is applied').toBeGreaterThanOrEqual(300);
});

for (const theme of THEMES) {
  test(`color-scheme is ${theme} and a native Lead Queue checkbox paints ${theme}`, async ({ app, page }) => {
    await app.setTheme(theme);
    await app.gotoRoute('/lead-queue');

    const scheme = await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme);
    expect(scheme).toBe(theme);
    await expect(page.locator('meta[name="color-scheme"]')).toHaveAttribute('content', 'dark light');

    const checkbox = 'table.tbl input[type="checkbox"]';
    await expect(page.locator(checkbox).first()).toBeVisible();
    expect(await page.locator(checkbox).first().evaluate((el) => getComputedStyle(el).colorScheme)).toBe(theme);
    const pixel = await centerPixel(page, page.locator(checkbox).first());
    const luminance = relativeLuminance(pixel);
    if (theme === 'dark') {
      expect(luminance, `checkbox fill rgb(${pixel.join(', ')}) must not be light in the dark theme`).toBeLessThan(0.3);
    } else {
      expect(luminance, `checkbox fill rgb(${pixel.join(', ')}) must be light in the light theme`).toBeGreaterThan(0.6);
    }
  });
}

test('meta theme-color follows the painted theme and the page background token', async ({ app, page }) => {
  const seen: Record<string, string | null> = {};
  for (const theme of THEMES) {
    await app.setTheme(theme);
    await app.gotoRoute('/');
    const [content, background] = await page.evaluate(() => [
      document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null,
      getComputedStyle(document.documentElement).getPropertyValue('--bg-0').trim(),
    ]);
    expect(content?.toUpperCase()).toBe(background.toUpperCase());
    seen[theme] = content;
  }
  expect(seen.dark).not.toBe(seen.light);
});

test('with nothing stored the app follows the OS, and the Console System option follows live flips', async ({ app, page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await app.gotoRoute('/');
  const html = page.locator('html');
  await expect(html, 'nothing stored + OS light boots light').toHaveAttribute('data-theme', 'light');

  const console_ = await app.openConsole();
  const themeGroup = console_.getByRole('group', { name: 'Theme' });
  await expect(themeGroup.getByRole('button', { name: 'System' })).toHaveAttribute('aria-pressed', 'true');

  await themeGroup.getByRole('button', { name: 'Dark' }).click();
  await expect(html, 'an explicit choice pins the theme').toHaveAttribute('data-theme', 'dark');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(html, 'an explicit choice ignores OS flips').toHaveAttribute('data-theme', 'dark');

  await themeGroup.getByRole('button', { name: 'System' }).click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  const lightChrome = await themeColorAndPageBackground(page);
  expect(lightChrome.meta, 'theme-color is the light page background').toBe(lightChrome.background);

  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(html, 'System follows an OS flip without a reload').toHaveAttribute('data-theme', 'dark');
  // theme-boot.js only sets theme-color once, at boot; after a live flip the
  // browser chrome follows only if AppContext re-syncs the meta.
  await expect
    .poll(async () => (await themeColorAndPageBackground(page)).meta, { message: 'theme-color follows the live OS flip' })
    .not.toBe(lightChrome.meta);
  const darkChrome = await themeColorAndPageBackground(page);
  expect(darkChrome.meta, 'theme-color is the dark page background').toBe(darkChrome.background);

  await page.emulateMedia({ colorScheme: 'light' });
  await expect(html).toHaveAttribute('data-theme', 'light');
  expect(await page.evaluate(() => window.localStorage.getItem('mip.theme'))).toBe('system');
});

test('the Administration appearance section marks and edits the same preference as the Console', async ({ app, page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await app.gotoRoute('/admin-config');
  const html = page.locator('html');
  await expect(html, 'nothing stored + OS light paints light').toHaveAttribute('data-theme', 'light');

  const main = page.locator('#main-content');
  await main.getByRole('button', { name: /Workspace appearance/ }).click();
  const adminTheme = main.locator('.appearance-body').getByRole('group', { name: 'Theme' });
  // The painted theme is Light, but what the user chose is System.
  await expect(adminTheme.getByRole('button', { name: 'System' })).toHaveAttribute('aria-pressed', 'true');
  await expect(adminTheme.getByRole('button', { name: 'Light' })).toHaveAttribute('aria-pressed', 'false');

  await adminTheme.getByRole('button', { name: 'Dark' }).click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  const consoleTheme = (await app.openConsole()).getByRole('group', { name: 'Theme' });
  await expect(consoleTheme.getByRole('button', { name: 'Dark' }), 'the Console shows the same choice').toHaveAttribute('aria-pressed', 'true');

  await adminTheme.getByRole('button', { name: 'System' }).click();
  await expect(html, 'System follows the light OS again').toHaveAttribute('data-theme', 'light');
  await expect(consoleTheme.getByRole('button', { name: 'System' })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => window.localStorage.getItem('mip.theme'))).toBe('system');
});

for (const theme of THEMES) {
  for (const accent of ACCENTS) {
    test(`${theme} + ${accent}: visible token focus ring and no axe violation beyond the ratchet`, async ({ app, page }) => {
      // Two settled route loads and two full-page axe passes: under a loaded
      // runner this exceeds the default 60 s budget (observed 1.4-1.7 min at
      // load average ~250). A slow budget, not a retry.
      test.slow();
      await app.setTheme(theme);
      await app.setAccent(accent);
      await app.gotoRoute('/');
      await expect(page.locator('html')).toHaveAttribute('data-accent', accent);

      // Keyboard first so a later script focus() keeps :focus-visible. The
      // first Tab lands on the skip link: text on --accent-fill at 4.5:1.
      await page.keyboard.press('Tab');
      const skip = page.locator(':focus');
      await expect(skip).toHaveClass(/\bsr-skip-link\b/);
      await settleTransitions(skip);
      const skipColors = await renderedColors(skip);
      const skipRatio = contrastRatio(skipColors.fg, skipColors.bg);
      expect(skipRatio, `skip link ${skipColors.color}: ${skipRatio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);

      // The primary CTA, at rest and hovered (a11y-01: dark + red read 3.62:1).
      const primary = page.locator('#main-content .btn--primary:not([disabled]):visible').first();
      for (const state of ['rest', 'hover'] as const) {
        if (state === 'hover') await primary.hover();
        await settleTransitions(primary);
        const colors = await renderedColors(primary);
        const ratio = contrastRatio(colors.fg, colors.bg);
        expect(ratio, `.btn--primary ${state}: ${colors.color} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      }
      await page.mouse.move(0, 0);

      const nav = page.getByRole('navigation', { name: 'Primary navigation' });
      // The brand link has a bespoke rule (.rail__brand:focus-visible); the
      // module links have none, so only the ONE global :focus-visible rule in
      // tokens.css can paint their ring. Both must be the token ring.
      await expectTokenRing(page, nav.getByRole('link', { name: 'Entrada home' }), 'rail brand (bespoke rule)');
      await expectTokenRing(page, nav.locator('a.rail__item').first(), 'rail module link (global rule)');

      // The shared gate: WCAG A/AA at every impact against the canonical
      // ratchet, which holds no entry for these scans since a11y-01's red CTA
      // entries were retired by --accent-fill; any violation fails.
      for (const [path, route] of [['/', 'home'], ['/lead-queue', 'lead-queue']] as const) {
        if (path !== '/') await app.gotoRoute(path);
        await expectAxeClean(page, { key: { route, state: 'default' }, theme, accent, known: KNOWN_VIOLATIONS });
      }
    });
  }
}

for (const theme of THEMES) {
  for (const accent of ACCENTS) {
    test(`${theme} + ${accent}: print paints the accent family monochrome`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.setAccent(accent);
      await app.gotoRoute('/');
      await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
      // Non-vacuity: on screen every pair's accent-ink is a hue, never the print ink.
      const screenInk = await asComputedRgb(page, 'var(--accent-ink)');
      expect(screenInk, 'screen accent-ink').not.toBe(PRINT_INK);

      await page.emulateMedia({ media: 'print' });
      const printed: Record<string, string> = {};
      for (const token of ['--accent-ink', '--accent-data', '--accent-fill', '--accent-fill-hover', '--accent', '--chip-text', '--chip-bg']) {
        printed[token] = await asComputedRgb(page, `var(${token})`);
      }
      // --accent-ink colours every accent text site that survives print
      // (.text-accent, .proof-component__score, .audit__ico, .uc-asset-link).
      expect(printed).toEqual({
        '--accent-ink': PRINT_INK,
        '--accent-data': PRINT_INK,
        '--accent-fill': PRINT_INK,
        '--accent-fill-hover': PRINT_INK,
        '--accent': PRINT_INK,
        '--chip-text': PRINT_INK,
        '--chip-bg': PRINT_PAPER,
      });
    });
  }
}

for (const theme of THEMES) {
  test(`${theme}: an accent switch repaints the primary button, never a sparkline or the choropleth`, async ({ app, mockApi, page }) => {
    // The map-encoding rollup that paints every class, so a lvl-4 state exists.
    mockApi.register(mapAllClassesFixture.method, mapAllClassesFixture.pattern, mapAllClassesFixture.handler);
    await app.setTheme(theme);
    await app.setAccent('red');
    await app.gotoRoute('/');
    const html = page.locator('html');
    await expect(html).toHaveAttribute('data-accent', 'red');
    const spark = page.locator('#main-content .kpi .spark__line[stroke="var(--accent-data)"]').first();
    const region = page.locator('#main-content path.map-region.lvl-4').first();
    const button = page.locator('#main-content .btn--primary').first();
    await expect(spark, 'an up-trend KPI sparkline').toBeAttached();
    await expect(region, 'a top-step state').toBeAttached();
    await expect(button).toBeAttached();
    const read = async () => {
      for (const target of [spark, region, button]) await settleTransitions(target);
      return {
        stroke: await spark.evaluate((el) => getComputedStyle(el).stroke),
        fill: await region.evaluate((el) => getComputedStyle(el).fill),
        button: await button.evaluate((el) => getComputedStyle(el).backgroundColor),
      };
    };
    const red = await read();
    expect(red.stroke, 'the sparkline paints the fixed data ink').toBe(await asComputedRgb(page, DATA_INK[theme]));
    const [r, g, b] = parseRgb(red.button);
    expect(r - Math.max(g, b), `the primary button paints the red accent family: ${red.button}`).toBeGreaterThan(80);

    const console_ = await app.openConsole();
    await console_.getByRole('button', { name: 'Accent bright' }).click();
    await expect(html).toHaveAttribute('data-accent', 'bright');
    const bright = await read();
    expect(bright.button, 'chrome follows the accent').not.toBe(red.button);
    expect(bright.stroke, 'the sparkline ignores the accent').toBe(red.stroke);
    expect(bright.fill, 'the choropleth ignores the accent').toBe(red.fill);
  });
}
