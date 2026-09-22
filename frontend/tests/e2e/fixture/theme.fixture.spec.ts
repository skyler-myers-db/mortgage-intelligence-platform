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
 *  e. every theme x accent pair paints a visible focus ring from the shared
 *     token and passes axe color-contrast on / and /lead-queue.
 *
 * States the axe loop never renders (warning copy, amber glyphs, the active
 * evidence-drawer tab, text-input focus) are proven in
 * contrastStates.fixture.spec.ts.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { asComputedRgb, centerPixel, relativeLuminance } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const ACCENTS = ['bright', 'teal', 'navy', 'red'] as const;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const TRANSPARENT = 'rgba(0, 0, 0, 0)';

/**
 * The one pair that cannot reach AA without changing a prototype DARK value:
 * white CTA text on brand red #FF3621 (design_files/index.html:165), 3.6:1
 * (a11y-01). Mirrors DOCUMENTED_EXCEPTIONS in tokenContrast.test.ts; the
 * test fails if the exception stops being observed (stale) or if anything
 * else fails in that combo.
 */
const DOCUMENTED_AXE_EXCEPTIONS: Record<string, RegExp> = {
  'dark/red': /foreground color: #ffffff, background color: #ff3621/,
};

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

async function colorContrastViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page })
    .withRules(['color-contrast'])
    // Decorative canvas background layer; same waiver as accessibility.spec.ts.
    .exclude('canvas')
    .analyze();
  return results.violations.flatMap((violation) =>
    violation.nodes.map((node) => `${violation.id} (${violation.impact}): ${node.target.join(' ')} — ${node.failureSummary?.split('\n')[1] ?? ''}`),
  );
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

for (const theme of THEMES) {
  for (const accent of ACCENTS) {
    test(`${theme} + ${accent}: visible token focus ring and no colour-contrast violations`, async ({ app, page }) => {
      await app.setTheme(theme);
      await seedStorage(page, 'mip.accent', accent);
      await app.gotoRoute('/');
      await expect(page.locator('html')).toHaveAttribute('data-accent', accent);

      // Keyboard first so a later script focus() keeps :focus-visible.
      await page.keyboard.press('Tab');
      const link = page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link').first();
      await link.focus();
      const ring = await link.evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          matchesFocusVisible: el.matches(':focus-visible'),
          outlineStyle: style.outlineStyle,
          outlineWidth: style.outlineWidth,
          outlineColor: style.outlineColor,
          token: style.getPropertyValue('--focus-ring-color').trim(),
        };
      });
      expect(ring.matchesFocusVisible).toBe(true);
      expect(ring.outlineStyle).toBe('solid');
      expect(ring.outlineWidth).toBe('2px');
      expect(ring.outlineColor).not.toBe(TRANSPARENT);
      // The rendered ring is the resolved token (compare through a probe so
      // hex and rgb() spellings meet in the same computed form).
      expect(ring.outlineColor).toBe(await asComputedRgb(page, ring.token));

      const exception = DOCUMENTED_AXE_EXCEPTIONS[`${theme}/${accent}`];
      let excepted = 0;
      for (const route of ['/', '/lead-queue']) {
        if (route !== '/') await app.gotoRoute(route);
        const violations = await colorContrastViolations(page);
        const remaining = violations.filter((violation) => {
          if (exception?.test(violation)) {
            excepted += 1;
            return false;
          }
          return true;
        });
        expect(remaining, `axe color-contrast on ${route}`).toEqual([]);
      }
      if (exception) expect(excepted, 'documented exception no longer observed: remove it').toBeGreaterThan(0);
    });
  }
}
