/**
 * Administration's section-nav scroll-spy on the rendered build (audit
 * critic-09, fix rounds): the link marked aria-current="location" is the
 * section a scroll or a followed link actually lands, and a followed
 * section gives the marker back to position once the user scrolls it off
 * its landing (fix round 2). Shared by
 * audit-ledger-presenter.fixture.spec.ts (c) and
 * admin-section-nav.cross-engine.fixture.spec.ts (WebKit too, where the admin
 * chunk's stylesheet applied after mount and moved the landing line).
 *
 * Call it on a fresh /admin-config load with no hash.
 */
import type { Page } from '@playwright/test';
import { expect } from './test';

/**
 * The section label the spy should mark at the landing line `line` (px below
 * the scroller's top edge): the first section, in page order (the nav's own
 * link order), whose box reaches below the line. Two candidates bracket the
 * spy's one-pixel slack (its band starts at ceil(line) + 1), so a section
 * edge within a pixel or two of the line cannot make oracle and spy disagree.
 */
async function sectionsAtLine(page: Page, line: number): Promise<{ marked: string | null; candidates: string[] }> {
  return page.locator('.main').evaluate((main, at) => {
    const top = main.getBoundingClientRect().top + main.clientTop;
    const links = [...main.querySelectorAll<HTMLAnchorElement>('.admin-section-nav a')];
    const firstBelow = (y: number) =>
      links.find((link) => {
        const box = document.getElementById(new URL(link.href).hash.slice(1))?.getBoundingClientRect();
        return box !== undefined && box.bottom - top > y;
      })?.textContent ?? '';
    const marked = links.find((link) => link.getAttribute('aria-current') === 'location');
    return { marked: marked?.textContent ?? null, candidates: [firstBelow(at - 1), firstBelow(at + 3)] };
  }, line);
}

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

  // ...until the user scrolls on their own (fix round 2): then position
  // decides again, even while part of the followed section is still in view.
  // The landing line is measured, not derived: where a followed section that
  // can reach it (Data operations) actually lands. Appearance cannot reach
  // it (the page end), so it is wheeled up from the end. The wheel is over
  // the followed section's own top-left padding (no inner scroller there, and
  // not the sideways-scrolling section nav), so it scrolls .main; no link is
  // followed and the hash does not change.
  const wheelAway = async (id: string, label: string, delta: number, line: number) => {
    const hash = new URL(page.url()).hash;
    await page.locator(`#${id}`).hover({ position: { x: 8, y: 8 } });
    await page.mouse.wheel(0, delta);
    await expect(async () => {
      const { marked, candidates } = await sectionsAtLine(page, line);
      expect(marked, `after following ${label} and wheeling ${delta}px it no longer holds the marker`).not.toBe(label);
      expect(candidates, `the marked section is the one at the landing line (${line}px)`).toContain(marked);
    }).toPass({ timeout: 10_000 });
    expect(new URL(page.url()).hash, 'a user scroll follows no link').toBe(hash);
  };

  await link('Data operations').click();
  await expect(link('Data operations')).toHaveAttribute('aria-current', 'location');
  const line = await page.locator('#data-operations').evaluate((el) => {
    const main = el.closest('.main') as HTMLElement;
    return el.getBoundingClientRect().top - main.getBoundingClientRect().top - main.clientTop;
  });
  await wheelAway('data-operations', 'Data operations', -450, line);

  await link('Appearance').click();
  await expect(link('Appearance')).toHaveAttribute('aria-current', 'location');
  await wheelAway('appearance', 'Appearance', -600, line);
}
