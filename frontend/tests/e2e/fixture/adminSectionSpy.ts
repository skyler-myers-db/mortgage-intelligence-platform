/**
 * Administration's section-nav scroll-spy on the rendered build (audit
 * critic-09, fix round): the link marked aria-current="location" is the
 * section a scroll or a followed link actually lands. Shared by
 * audit-ledger-presenter.fixture.spec.ts (c) and
 * admin-section-nav.cross-engine.fixture.spec.ts (WebKit too, where the admin
 * chunk's stylesheet applied after mount and moved the landing line).
 *
 * Call it on a fresh /admin-config load with no hash.
 */
import type { Page } from '@playwright/test';
import { expect } from './test';

export async function expectSectionSpyMarksLandings(page: Page): Promise<void> {
  const sections = page.getByRole('navigation', { name: 'Administration sections' });
  const link = (label: string) => sections.getByRole('link', { name: label, exact: true });

  // With no link followed (no hash change, so nothing is rebuilt), a section
  // scrolled to the landing line as a focus scroll does (scroll-margin,
  // focus-ring allowance included) is the one marked: never Live probes by
  // its last pixels above the line, and Data estate through the loaded panel
  // that replaced the skeleton the spy first saw. Offer rules goes first so
  // Data estate's marker cannot be left over from the page load.
  await expect(page.locator('#data-estate[aria-busy="true"]')).toHaveCount(0);
  for (const [id, label] of [
    ['offer-rules', 'Offer rules'],
    ['data-estate', 'Data estate'],
  ] as const) {
    await page.locator(`#${id}`).evaluate((el) => el.scrollIntoView({ block: 'start' }));
    await expect(link(label), `${label} is marked once on the line`).toHaveAttribute('aria-current', 'location');
  }

  // A followed link marks its own section: Audit ledger and Data sources
  // share Offer rules' grid row, and Appearance cannot scroll up to the line.
  for (const label of ['Audit ledger', 'Data sources', 'Data estate', 'Offer rules', 'Appearance']) {
    await link(label).click();
    await expect(link(label), `${label} is marked current once it lands`).toHaveAttribute('aria-current', 'location');
  }
}
