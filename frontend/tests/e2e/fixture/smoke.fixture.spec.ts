/**
 * Fixture smoke: every route x {dark, light} renders against the mock API.
 *
 * This spec only asserts what is true today. Later lanes add the assertions
 * that accompany their fixes (geometry, contrast, interaction) in their own
 * *.fixture.spec.ts files; known defects are not pinned here.
 */
import { ERROR_SURFACE_SELECTOR } from './app';
import { FIXTURE_ROUTES, FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoSurfaceOverflow } from './visual';

for (const theme of FIXTURE_THEMES) {
  for (const route of FIXTURE_ROUTES) {
    test(`${route.name} renders in ${theme}`, async ({ app, hygiene, mockApi, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(route.path);

      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      const main = page.locator('#main-content');
      const heading = main.locator('h1');
      await expect(heading).toHaveCount(1);
      await expect(heading).toBeVisible();
      await expect(heading).not.toBeEmpty();
      if (route.populated) {
        await expect(main, 'the route rendered fixture data, not just its chrome').toContainText(route.populated);
      }

      await expect(page.locator(ERROR_SURFACE_SELECTOR), 'a healthy route renders no error surface').toHaveCount(0);

      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(overflow.scrollWidth, 'the document must not scroll horizontally').toBeLessThanOrEqual(overflow.clientWidth);
      // No `.surface` scrolls sideways either (beyond visual.ts's dated
      // KNOWN_SURFACE_OVERFLOW ratchet); runs on every host, unlike the VRT.
      await expectNoSurfaceOverflow(page, { route: route.name, state: 'default', theme });

      expect(hygiene.active, 'the hygiene fixture is attached').toBe(true);
      expect(hygiene.documentsWithCsp, 'the production CSP was served with the document').toBeGreaterThan(0);
      expect(mockApi.calls.length, 'the route was answered by the mock API').toBeGreaterThan(0);
      expect(mockApi.unregistered, 'every API call has a registered fixture').toEqual([]);
      expect(hygiene.violations()).toEqual([]);
    });
  }
}
