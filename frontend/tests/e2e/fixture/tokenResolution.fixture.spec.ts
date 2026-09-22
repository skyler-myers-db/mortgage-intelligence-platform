/**
 * Design tokens resolved at the COMPUTED layer, in the production build
 * (2026-09-21 audit responsive-02). tokenContrast.test.ts and
 * tokenUsage.test.ts model the cascade from the CSS text; these tests ask the
 * browser what it actually painted, so a token that is defined but never
 * reaches the element (a per-selector override shadows a token, a modifier
 * loses to its base rule on source order) fails here even when the
 * source-level gates pass.
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
