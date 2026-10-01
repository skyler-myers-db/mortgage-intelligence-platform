/**
 * Administration's section-nav scroll-spy across engines (audit critic-09,
 * fix round). Runs in fixture-chromium on every fixture run and in
 * fixture-webkit in the e2e-cross-engine CI job (MIP_CROSS_ENGINE=1), at
 * 1440x900. The assertions are fixture/adminSectionSpy.ts, shared with
 * audit-ledger-presenter.fixture.spec.ts (c).
 *
 * WebKit defect found by the fix round: the admin chunk's stylesheet (the
 * section nav's scroll-margin) applied after the nav mounted, so a landing
 * line read once at mount was the shell's 61px, not 102px, and a section
 * scrolled to the line left Live probes marked (4 of 4 local runs). The spy
 * now re-reads the line on every callback and rebuilds its observers when it
 * has moved; Chromium applied the stylesheet first and never showed it.
 *
 * Fix round 2: a followed section keeps the marker only until the user
 * scrolls it off its landing (adminSectionSpy.ts wheels .main after following
 * 'Data operations' and 'Appearance'), and a cold section link focuses the
 * Data estate skeleton, which carries the section id before the panel loads.
 */
import { expectSectionSpyMarksLandings } from './adminSectionSpy';
import { expect, test } from './test';

test('the section nav marks the section a scroll or a followed link lands', async ({ app, page }) => {
  await app.gotoRoute('/admin-config');

  await expectSectionSpyMarksLandings(page);
});

test('a cold #data-estate link focuses the Data estate skeleton while the panel loads', async ({ app, page }) => {
  // Hold the proof read so the skeleton is what the hash effect focuses; the
  // harness's mock answers it once released.
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    (url) => /\/api\/(v1\/)?data-estate$/.test(url.pathname),
    async (route) => {
      await held;
      await route.fallback();
    },
  );

  await page.goto('/admin-config#data-estate', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#data-estate[aria-busy="true"]'), 'a focusable section root, loading or not').toBeFocused();

  release();
  await app.settle();
  await expect(page.locator('#data-estate')).not.toHaveAttribute('aria-busy', 'true');
});
