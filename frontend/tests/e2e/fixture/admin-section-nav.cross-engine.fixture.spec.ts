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
 */
import { expectSectionSpyMarksLandings } from './adminSectionSpy';
import { test } from './test';

test('the section nav marks the section a scroll or a followed link lands', async ({ app, page }) => {
  await app.gotoRoute('/admin-config');

  await expectSectionSpyMarksLandings(page);
});
