/**
 * The W5e Lead Queue layout fixes from the VRT review of CI run 40311237233,
 * proven on the rendered production build at 1440x900 in both themes:
 *
 *  (1) The filter omnibox pill, icon-only up to a 1480px `main`, keeps the
 *      text pills' block size in both densities, Console closed and open, so
 *      it lines up with STATE and its popover opens below the whole filter
 *      row: STATE's bottom border is still the topmost paint (the 25px pill
 *      hung the popover over the bottom 3px of STATE and SEGMENT; 22px
 *      against 29px in compact).
 *  (2) The Approved chip sits on its row's centre, level with the row's
 *      Status chip, and the icon-only Revoke's chevron centres on it (as
 *      bare inline siblings the textless button dropped the chip about 2px
 *      on Linux).
 *  (3) The expanded row's Primary offer actions keep one line, Console
 *      closed and open, with Linux Chromium's wider text emulated off Linux,
 *      and the approval banner keeps its place: its "Approving as" line on
 *      screen and its Approve reaching into view with the Console closed
 *      ("More like this" as a fourth action wrapped the row, grew the card
 *      38px and pushed both below the fold).
 *  (4) "More like this" shows what it opens: after the click its panel's
 *      count line and links are wholly in the viewport and its trigger
 *      stays there, in the queue's row preview (Console closed: the
 *      approval banner's "Approving as" line and Approve in view too), in
 *      the Triage deck (its Review stays in view) and on Borrower 360. On
 *      the explore row at the table scroller's bottom edge the panel opened
 *      wholly below the fold (count line at 958 of 900 in the queue, 1005 in
 *      the deck), and on Borrower 360 its links did; only aria-expanded
 *      changed on screen.
 */
import type { Locator, Page } from '@playwright/test';
import { FIXTURE_DENSITIES } from './app';
import { PRIMARY_BORROWER } from './data/borrowers';
import { APPROVED_LEAD } from './data/revoke';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

/** The core FilterSelect pills (text pills: an uppercase label and a mono value). */
const TEXT_PILLS = ['STATE', 'SEGMENT', 'RELATIONSHIP', 'PRODUCT', 'APPROVAL'] as const;

/**
 * Linux Chromium draws Geist and Geist Mono wider than macOS (whole-pixel
 * advances); the extra letter-spacing over-approximates it on every button
 * label of the expanded row, so a one-line fit measured here holds on CI.
 * Applying it on Linux would double-count (followups-snooze.fixture.spec.ts).
 */
const LINUX_TEXT_EMULATION = '.tbl__expand-inner--lead .btn { letter-spacing: 0.5px; }';

interface Box { top: number; bottom: number; height: number; centre: number }

async function box(target: Locator): Promise<Box> {
  return target.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom, height: rect.height, centre: rect.top + rect.height / 2 };
  });
}

function pill(page: Page, label: string): Locator {
  return page.locator(`.filter-row--lead-queue button[aria-haspopup="listbox"][aria-label^="${label}:"]`);
}

/**
 * An open More like this panel's own content is wholly on screen: its count
 * line (the state's role=status) and its links, with room under them for a
 * link's focus ring before the clip edge of every scroller they are in (the
 * panel's scroll-margin-block-end); and the trigger the person clicked is
 * still in view.
 */
async function expectSimilarPanelInView(trigger: Locator, panel: Locator, phase: string): Promise<void> {
  await expect(panel.locator('[role="status"]'), `${phase}: the panel's count`).toHaveText(/match this pattern/);
  await expect(panel.locator('[role="status"]'), `${phase}: the panel's count line is on screen`).toBeInViewport({ ratio: 1 });
  const links = panel.getByRole('link');
  expect(await links.count(), `${phase}: the panel carries its links`).toBeGreaterThanOrEqual(1);
  for (const link of await links.all()) {
    await expect(link, `${phase}: the panel's link "${await link.textContent()}" is on screen`).toBeInViewport({ ratio: 1 });
  }
  const ringRoom = await links.last().evaluate((link) => {
    const root = getComputedStyle(document.documentElement);
    const ring = parseFloat(root.getPropertyValue('--focus-ring-width')) + parseFloat(root.getPropertyValue('--focus-ring-offset'));
    let clip = window.innerHeight;
    for (let el = link.parentElement; el; el = el.parentElement) {
      const style = getComputedStyle(el);
      if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowY)) {
        clip = Math.min(clip, el.getBoundingClientRect().bottom - parseFloat(style.borderBottomWidth));
      }
    }
    return clip - (link.getBoundingClientRect().bottom + ring);
  });
  expect(ringRoom, `${phase}: a focused link's ring clears the clip edge (${ringRoom}px to spare)`).toBeGreaterThanOrEqual(0);
  // The reveal scrolls no further than the panel: the trigger is on screen
  // and not under the table's sticky header or the docked route nav.
  await expect(trigger, `${phase}: the trigger stays on screen`).toBeInViewport({ ratio: 1 });
  const triggerOnTop = await trigger.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return hit !== null && (hit === el || el.contains(hit));
  });
  expect(triggerOnTop, `${phase}: the trigger is the topmost paint at its centre`).toBe(true);
}

/** Distinct rounded tops of a flex row's items: one entry means one line. */
async function lineTops(items: Locator): Promise<number[]> {
  return items.evaluateAll((elements) => [...new Set(elements.map((el) => Math.round(el.getBoundingClientRect().top)))]);
}

async function expectOmniboxLinedUp(page: Page, phase: string): Promise<void> {
  const omnibox = page.getByTestId('lead-queue-omnibox');
  await expect(page.locator('.lead-queue-omnibox__label'), `${phase}: precondition, the pill is icon-only`).toBeHidden();
  const own = await box(omnibox);
  for (const label of TEXT_PILLS) {
    const text = await box(pill(page, label));
    expect(Math.abs(own.height - text.height), `${phase}: the omnibox pill is as tall as ${label} (${own.height} vs ${text.height})`)
      .toBeLessThanOrEqual(0.5);
  }
  // The row may wrap (Console open): STATE is the pill's neighbour on its line.
  const state = await box(pill(page, 'STATE'));
  expect(Math.abs(own.top - state.top), `${phase}: the omnibox pill shares STATE's top`).toBeLessThanOrEqual(0.5);

  await omnibox.click();
  const panel = page.getByTestId('lead-queue-omnibox-panel');
  await expect(panel).toBeVisible();
  // Every item on the pill's flex line ends above the popover.
  const rowBottom = await page.locator('.filter-row--lead-queue > *').evaluateAll((items, top) => Math.max(
    ...items
      .map((item) => item.getBoundingClientRect())
      .filter((rect) => rect.height > 0 && Math.abs(rect.top - top) <= 0.5)
      .map((rect) => rect.bottom),
  ), own.top);
  const popover = await box(panel);
  expect(popover.top, `${phase}: the popover opens below the filter row (row bottom ${rowBottom})`).toBeGreaterThanOrEqual(rowBottom);
  // ...and STATE's bottom border is still painted on top, not under the popover.
  const stateBorder = await pill(page, 'STATE').evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.bottom - 0.5);
    return hit !== null && (hit === el || el.contains(hit));
  });
  expect(stateBorder, `${phase}: STATE's bottom edge is the topmost paint with the popover open`).toBe(true);
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
}

for (const theme of FIXTURE_THEMES) {
  for (const density of FIXTURE_DENSITIES) {
    test(`(1) the icon-only omnibox pill is as tall as the text pills and its popover clears the row, Console closed and open (${theme}, ${density})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.setDensity(density);
      await app.gotoRoute('/lead-queue');
      await expectOmniboxLinedUp(page, 'Console closed');
      await app.openConsole();
      await app.settle();
      await expectOmniboxLinedUp(page, 'Console open');
    });

    test(`(2) the Approved chip sits on its row's centre and Revoke's chevron centres on it (${theme}, ${density})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.setDensity(density);
      await app.gotoRoute('/lead-queue');
      const cell = page.getByTestId(`lead-approval-cell-${APPROVED_LEAD.borrower_id}`);
      const revoke = page.getByTestId(`lead-revoke-${APPROVED_LEAD.borrower_id}`);
      await expect(revoke).toBeVisible();
      const row = await box(cell.locator('xpath=ancestor::tr[1]'));
      const chip = await box(cell.locator('.chip--success'));
      const chevron = await box(revoke.locator('svg'));
      const status = await box(page.getByTestId(`lead-status-${APPROVED_LEAD.borrower_id}`).locator('.chip').first());
      const measured = JSON.stringify({ row: row.centre, chip: chip.centre, chevron: chevron.centre, status: status.centre });
      expect(Math.abs(chip.centre - row.centre), `the Approved chip on the row's centre ${measured}`).toBeLessThanOrEqual(1);
      expect(Math.abs(chip.centre - status.centre), `the Approved chip level with the row's Status chip ${measured}`).toBeLessThanOrEqual(1);
      expect(Math.abs(chevron.centre - chip.centre), `Revoke's chevron centred on the chip ${measured}`).toBeLessThanOrEqual(1);
    });
  }

  for (const phase of ['Console closed', 'Console open'] as const) {
    test(`(3) the expanded row's offer actions keep one line and the approval banner keeps its place (${theme}, ${phase})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      if (phase === 'Console open') {
        await app.openConsole();
        await app.settle();
      }
      const row = await app.expandFirstLeadRow();
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
      const actions = row.locator('.preview-offer-card .chip-row > *');
      await expect(actions.filter({ hasText: 'Save lead' })).toBeVisible();
      expect(await actions.count(), 'the Primary offer card carries its actions').toBeGreaterThanOrEqual(3);
      expect(await lineTops(actions), `${phase}: the Primary offer actions keep one line`).toHaveLength(1);
      // More like this stays one click from the row, on the explore row beside Ask Genie.
      const similar = row.getByRole('button', { name: 'More like this' });
      await expect(similar).toBeVisible();
      expect(await lineTops(row.locator('.chip-row:has([data-testid^="lead-more-like-this-"]) > *')), `${phase}: the explore row keeps one line`)
        .toHaveLength(1);

      if (phase === 'Console closed') {
        const banner = row.getByTestId(/^lead-row-approval-/);
        await expect(banner.getByTestId('approval-actor'), 'the banner names the approver on screen').toBeInViewport({ ratio: 1 });
        await expect(banner.getByRole('button', { name: /^Approve/ }), 'the banner\'s Approve reaches into view').toBeInViewport();
      }
    });

    test(`(4) More like this opens its panel in view in the row preview and the Triage deck (${theme}, ${phase})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      if (phase === 'Console open') {
        await app.openConsole();
        await app.settle();
      }
      const row = await app.expandFirstLeadRow();
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
      const trigger = row.getByRole('button', { name: 'More like this' });
      await trigger.click();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await expectSimilarPanelInView(trigger, row.locator('.similar-cohort'), `${phase}, row preview`);
      if (phase === 'Console closed') {
        // The reveal scrolls the table no further than the panel: the banner stays.
        const banner = row.getByTestId(/^lead-row-approval-/);
        await expect(banner.getByTestId('approval-actor'), 'the banner still names the approver on screen').toBeInViewport({ ratio: 1 });
        await expect(banner.getByRole('button', { name: /^Approve/ }), 'the banner\'s Approve is on screen').toBeInViewport({ ratio: 1 });
      }

      // The Triage deck shows the same preview, in `.main` (no table scroller).
      await page.getByTestId('lead-triage-enter').click();
      const card = page.locator('.triage__card');
      await expect(card).toBeVisible();
      const deckTrigger = card.getByRole('button', { name: 'More like this' });
      await deckTrigger.click();
      await expect(deckTrigger).toHaveAttribute('aria-expanded', 'true');
      await expectSimilarPanelInView(deckTrigger, card.locator('.similar-cohort'), `${phase}, Triage deck`);
      await expect(page.getByTestId('triage-review'), `${phase}: the deck's Review stays on screen`).toBeInViewport({ ratio: 1 });
    });
  }

  test(`(4) More like this opens its panel in view on Borrower 360 (${theme})`, async ({ app, page }) => {
    await app.setTheme(theme);
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    const trigger = page.getByTestId('borrower-more-like-this');
    await trigger.click();
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await expectSimilarPanelInView(trigger, page.locator('.similar-cohort'), 'Borrower 360');
  });
}
