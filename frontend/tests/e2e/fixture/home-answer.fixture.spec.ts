/**
 * Home answers its own question (lane home-answer: 2026-09-21 audit flow-05,
 * visual-06, flow-07 CTA copy). Rendered-layer proofs at 1440x900, both
 * themes, against the production build:
 *
 *  - KPI values are at least the size of the page title.
 *
 * Mutation check (reported in the lane summary): restoring the KPI clamp at
 * 1440 fails "KPI values are at least the size of the page title".
 */
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

for (const theme of FIXTURE_THEMES) {
  test.describe(`Home answer band (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
    });

    test('KPI values are at least the size of the page title', async ({ page }) => {
      const sizes = await page.evaluate(() => {
        const px = (el: Element | null) => (el ? Number.parseFloat(getComputedStyle(el).fontSize) : Number.NaN);
        return {
          h1: px(document.querySelector('#main-content h1')),
          kpis: Array.from(document.querySelectorAll('#main-content .kpi__value')).map(px),
        };
      });
      expect(sizes.kpis).toHaveLength(4);
      for (const size of sizes.kpis) {
        expect(size, `KPI ${size}px vs H1 ${sizes.h1}px`).toBeGreaterThanOrEqual(sizes.h1);
        expect(size, 'the prototype KPI value is --fs-36').toBe(36);
      }
    });
  });
}
