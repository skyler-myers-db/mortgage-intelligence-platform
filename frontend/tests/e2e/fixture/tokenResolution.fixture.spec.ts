/**
 * Design tokens resolved at the COMPUTED layer, in the production build
 * (2026-09-21 audit motion-04, responsive-02). tokenContrast.test.ts and
 * tokenUsage.test.ts model the cascade from the CSS text; these tests ask the
 * browser what it actually painted, so a token that is defined but never
 * reaches the element (an undefined alias voids the whole `transition`
 * shorthand at computed-value time; a per-selector override shadows a
 * token) fails here even when the source-level gates pass.
 */
import { asComputedRgb, contrastRatio, parseRgb, tokenValue } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

/**
 * Status-coloured text whose light-theme colour used to be patched per
 * selector (`[data-theme="light"] .chip--warning { color: var(--entrada-navy) }`
 * and eleven more). Each must now paint its token ink, which carries the
 * light value itself.
 */
const STATUS_INK_SITES: ReadonlyArray<{ className: string; token: string }> = [
  { className: 'chip chip--success', token: '--status-success-ink' },
  { className: 'chip chip--warning', token: '--status-warning-ink' },
  { className: 'chip chip--danger', token: '--status-danger-ink' },
  { className: 'score score--high', token: '--status-success-ink' },
  { className: 'score score--med', token: '--status-warning-ink' },
  { className: 'borrower-story__verdict borrower-story__verdict--ok', token: '--status-success-ink' },
  { className: 'borrower-story__verdict borrower-story__verdict--warn', token: '--status-warning-ink' },
  { className: 'portfolio-summary__verdict portfolio-summary__verdict--ok', token: '--status-success-ink' },
  { className: 'portfolio-summary__verdict portfolio-summary__verdict--warn', token: '--status-warning-ink' },
  { className: 'audit__ico green', token: '--status-success-ink' },
  { className: 'audit__ico red', token: '--status-danger-ink' },
  { className: 'map-legend__caption map-legend__caption--degraded', token: '--status-danger-ink' },
];

test('--ease-standard resolves on the Cmd-K keycap transition (motion-04)', async ({ app, page }) => {
  // `.topbar__search-kbd` transitions `color` and `border-color` with
  // `var(--dur-fast) var(--ease-standard)`. Before the alias was defined the
  // shorthand was invalid at computed-value time and the browser fell back to
  // the initial `all 0s ease`, so the keycap snapped instead of easing.
  // The harness defaults to reduced motion, whose reset collapses every
  // duration; this test is about the declared transition, so opt out.
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await app.gotoRoute('/');
  const keycap = page.getByRole('banner').getByRole('button', { name: /^Open command palette/ });
  await expect(keycap).toBeVisible();
  const computed = await keycap.evaluate((el) => {
    const style = getComputedStyle(el);
    const ease = style.getPropertyValue('--ease').trim();
    // Custom properties serialize as written (the build minifies `0.2` to
    // `.2`); a probe gives the curve in the computed-value form.
    const probe = document.createElement('span');
    probe.style.transitionTimingFunction = ease;
    document.body.appendChild(probe);
    const easeComputed = getComputedStyle(probe).transitionTimingFunction;
    probe.remove();
    return {
      property: style.transitionProperty,
      duration: style.transitionDuration,
      timing: style.transitionTimingFunction,
      ease,
      easeComputed,
      standard: style.getPropertyValue('--ease-standard').trim(),
    };
  });
  expect(computed.easeComputed).toMatch(/^cubic-bezier\(/);
  expect(computed.standard, '--ease-standard is an alias of --ease').toBe(computed.ease);
  expect(computed.property).toBe('color, border-color');
  expect(computed.duration).toBe('0.12s, 0.12s');
  expect(computed.timing).toBe(`${computed.easeComputed}, ${computed.easeComputed}`);
});

test('light: the medium score band paints the token ink, not a per-selector override (responsive-02)', async ({ app, page }) => {
  // The light theme used to patch `.score--med` (and `.chip--warning`) per
  // selector to navy, hiding the token-level `--status-warning-ink`: the
  // prototype's own light value, design_files/index.html:416 (#B45309).
  await app.setTheme('light');
  await app.gotoRoute('/lead-queue');
  const badge = page.locator('table.tbl .score--med').first();
  await expect(badge).toBeVisible();
  const ink = await tokenValue(badge, '--status-warning-ink');
  expect(ink.toUpperCase()).toBe('#B45309');
  expect(await badge.evaluate((el) => getComputedStyle(el).color)).toBe(await asComputedRgb(page, ink));
});

for (const theme of THEMES) {
  test(`${theme}: every status-coloured text site paints its token ink at AA (responsive-02)`, async ({ app, page }) => {
    await app.setTheme(theme);
    await app.gotoRoute('/');
    const root = page.locator('html');
    const expected: Record<string, string> = {};
    for (const site of STATUS_INK_SITES) {
      expected[site.className] = await asComputedRgb(page, await tokenValue(root, site.token));
    }

    const painted = await page.evaluate((classLists) => {
      const host = document.createElement('div');
      document.body.appendChild(host);
      const colors = classLists.map((className) => {
        const probe = document.createElement('span');
        probe.className = className;
        probe.textContent = 'Status';
        host.appendChild(probe);
        return [className, getComputedStyle(probe).color] as const;
      });
      host.remove();
      return Object.fromEntries(colors);
    }, STATUS_INK_SITES.map((site) => site.className));
    expect(painted).toEqual(expected);

    for (const surface of ['--bg-1', '--bg-2']) {
      const bg = parseRgb(await asComputedRgb(page, await tokenValue(root, surface)));
      for (const [className, color] of Object.entries(painted)) {
        const ratio = contrastRatio(parseRgb(color), bg);
        expect(ratio, `${className}: ${color} on ${surface} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
}
