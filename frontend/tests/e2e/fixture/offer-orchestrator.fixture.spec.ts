/**
 * Offer Orchestrator (lane offer-orchestrator, 2026-09-21 audit visual-v1 and
 * critic-02). Rendered-layer proofs on the production build at 1440x900.
 *
 * visual-v1: the loan-officer routing and the ApprovalBanner used to sit
 * ~480px below the fold beside a half-empty Primary offer card, and a hero
 * Approve shortcut let a reviewer approve before the routing was ever on
 * screen. Now the left column stacks the offer's reasoning, the routing and
 * the gate dock in one sticky bar at the bottom of `.main`, and the hero has
 * no Approve. Pinned here: without scrolling, Approve and the routing
 * selector are fully inside the viewport and covered by nothing (not the
 * Console, not the Genie launcher, not the open Genie panel); scrolling keeps
 * the bar docked; at the end of the page it rests above the footer; focus from
 * below the view stops above it, while focus inside it (a click on the routing
 * select, Reject opening its rationale, Cancel handing focus back) scrolls
 * nothing; the reject rationale opens inside it, focused, and Cancel returns
 * focus to Reject. Under browser zoom (200% and 150%) the
 * bar is in flow, and the offer and every paragraph of the certified copy can
 * be read at some scroll position (WCAG 1.4.10); 1366x768 at 100% still docks.
 *
 * critic-02 (part 2): the certified copy is framed the way the borrower
 * receives it. Pinned here in both themes: the email frame's From / To (the
 * masked id as a synthetic contact) / Subject header, a long subject that
 * wraps inside the frame, a body whose rendered text is exactly the audited
 * copy, the disclosure, and the SMS bubble with its carrier segment count
 * (GSM-7 and UCS-2); axe finds nothing in either frame.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import { EMAIL_BODY, GSM_SMS_BODY, LONG_SUBJECT, UCS2_SMS_BODY, registerDraftCopy } from './data/offerOrchestrator';
import { LENDER_NAME } from './data/reference';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const ROUTE = `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no bounding box');
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

function viewportBox(page: Page): Box {
  const size = page.viewportSize();
  if (!size) throw new Error('no viewport');
  return { left: 0, top: 0, right: size.width, bottom: size.height };
}

/** The element painted at the control's centre is the control (or inside it): nothing covers it. */
async function isUncovered(control: Locator): Promise<boolean> {
  return control.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return hit !== null && (hit === element || element.contains(hit));
  });
}

async function mainScroll(page: Page): Promise<{ top: number; max: number; bottom: number }> {
  return page.locator('.main').evaluate((main) => ({
    top: main.scrollTop,
    max: main.scrollHeight - main.clientHeight,
    bottom: main.getBoundingClientRect().top + main.clientHeight,
  }));
}

/** The decision controls the brief names, inside the docked bar. */
function decisionControls(page: Page): { bar: Locator; approve: Locator; reject: Locator; routing: Locator; followUp: Locator } {
  const bar = page.getByRole('region', { name: 'Routing and approval' });
  return {
    bar,
    approve: bar.getByRole('button', { name: 'Approve outreach' }),
    reject: bar.getByRole('button', { name: 'Reject', exact: true }),
    routing: bar.getByRole('combobox', { name: 'Assign to loan officer' }),
    followUp: bar.getByRole('combobox', { name: 'Follow-up reminder' }),
  };
}

async function expectInsideViewport(page: Page, control: Locator, name: string): Promise<void> {
  const box = await boxOf(control);
  const view = viewportBox(page);
  expect(box.top, `${name} top is inside the viewport`).toBeGreaterThanOrEqual(view.top);
  expect(box.bottom, `${name} bottom is inside the viewport`).toBeLessThanOrEqual(view.bottom);
  expect(box.left, `${name} left is inside the viewport`).toBeGreaterThanOrEqual(view.left);
  expect(box.right, `${name} right is inside the viewport`).toBeLessThanOrEqual(view.right);
  expect(await isUncovered(control), `${name} is not covered by another layer`).toBe(true);
}

test.describe('decision bar (visual-v1)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: Approve and the routing selector are on screen at 1440x900 without scrolling`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      const { bar, approve, reject, routing, followUp } = decisionControls(page);
      await expect(approve).toBeEnabled();
      await expect(bar.locator('.approval')).toHaveCount(1);
      expect((await mainScroll(page)).top, 'precondition: the page has not scrolled').toBe(0);
      expect((await mainScroll(page)).max, 'precondition: the page is taller than the viewport').toBeGreaterThan(0);
      // At 1440 the floating launcher (`.genie__fab`) is not displayed (the
      // topbar toggle is the desktop entry), so no launcher can sit over the
      // bar; the FAB overlap is proven at 700px below, the open panel in the
      // Genie test.
      await expect(page.locator('.genie__fab'), 'precondition: no floating Genie launcher at 1440').toBeHidden();

      for (const [name, control] of [['Approve', approve], ['Reject', reject], ['routing selector', routing], ['follow-up selector', followUp]] as const) {
        await expectInsideViewport(page, control, name);
      }
      // Docked: the bar ends on `.main`'s bottom edge while the page is above its end.
      expect(Math.abs((await boxOf(bar)).bottom - (await mainScroll(page)).bottom)).toBeLessThanOrEqual(1);

      // The hero carries no Approve any more: the only one is the gate's, beside the routing.
      await expect(page.locator('.proto-hero__actions').getByRole('button', { name: /approve/i })).toHaveCount(0);
      await expect(page.locator('#main-content').getByRole('button', { name: /^Approve/ })).toHaveCount(1);
    });
  }

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: with the Console open the bar stays clear of it and in view`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      const panel = await app.openConsole();
      const consoleBox = await boxOf(panel);
      const { bar, approve, routing } = decisionControls(page);
      await expect(approve).toBeEnabled();
      for (const [name, control] of [['Approve', approve], ['routing selector', routing]] as const) {
        await expectInsideViewport(page, control, name);
        expect(overlaps(await boxOf(control), consoleBox), `${name} overlaps the Console`).toBe(false);
      }
      expect(overlaps(await boxOf(bar), consoleBox), 'the bar runs under the Console').toBe(false);
    });
  }

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: with the Genie panel open, Reject and Approve reflow clear of it`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      const { bar, approve, reject, routing } = decisionControls(page);
      await expect(approve).toBeEnabled();
      const barHeight = async () => { const box = await boxOf(bar); return box.bottom - box.top; };
      const closedHeight = await barHeight();

      // The docked panel sits over the bar's inline end, where the gate's
      // buttons are. Measure it once its entry transform has settled.
      const expectClearOfGenie = async (dialog: Locator, where: string) => {
        await expect.poll(() => dialog.evaluate((node) => getComputedStyle(node).transform), { message: 'Genie entry settled' })
          .toMatch(/^(none|matrix\(1, 0, 0, 1, 0, 0\))$/);
        await expect.poll(() => isUncovered(approve), { message: `${where}: Approve is not under the Genie panel` }).toBe(true);
        const panel = await boxOf(dialog);
        expect(overlaps((await boxOf(bar)), panel), `precondition (${where}): the panel reaches over the bar`).toBe(true);
        for (const [name, control] of [['Approve', approve], ['Reject', reject], ['routing selector', routing]] as const) {
          await expectInsideViewport(page, control, `${name} (${where})`);
          expect(overlaps(await boxOf(control), panel), `${name} overlaps the Genie panel (${where})`).toBe(false);
        }
      };

      await expectClearOfGenie(await app.openGenie(), 'Console closed');
      // Closing Genie gives the space back: the bar returns to its own height.
      await page.getByRole('banner').getByRole('button', { name: 'Toggle Genie chat' }).click();
      await expect(page.getByRole('dialog', { name: 'Genie chat' })).toBeHidden();
      await expect.poll(barHeight, { message: 'bar height after Genie closes (px)' }).toBe(closedHeight);

      // The docked panel moves left of an open Console; the bar follows it.
      const consolePanel = await app.openConsole();
      await expectClearOfGenie(await app.openGenie(), 'Console open');
      expect(overlaps(await boxOf(approve), await boxOf(consolePanel)), 'Approve overlaps the Console').toBe(false);
    });
  }

  test('where the floating Genie launcher shows, Approve stays clear of it', async ({ app, page }) => {
    await page.setViewportSize({ width: 700, height: 900 });
    await app.gotoRoute(ROUTE);
    const fab = page.locator('.genie__fab');
    await expect(fab, 'precondition: the FAB is displayed below 721px').toBeVisible();
    const { bar, approve, routing } = decisionControls(page);
    await expect(approve).toBeEnabled();
    // Non-vacuous: the FAB sits over the docked bar's box, so only the bar's
    // inline-end clearance keeps the controls out from under it.
    expect(overlaps(await boxOf(bar), await boxOf(fab)), 'precondition: the FAB is over the docked bar').toBe(true);
    for (const [name, control] of [['Approve', approve], ['routing selector', routing]] as const) {
      await expectInsideViewport(page, control, name);
      expect(overlaps(await boxOf(control), await boxOf(fab)), `${name} overlaps the Genie FAB`).toBe(false);
    }
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: scrolling keeps the bar docked, and at the end it rests above the footer`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      const { bar, approve } = decisionControls(page);
      await expect(approve).toBeEnabled();
      const { max } = await mainScroll(page);
      expect(max, 'precondition: the page scrolls').toBeGreaterThan(100);

      await page.locator('.main').evaluate((main, to) => { main.scrollTop = to; }, Math.round(max / 2));
      const middle = await mainScroll(page);
      expect(middle.top).toBeGreaterThan(0);
      expect(Math.abs((await boxOf(bar)).bottom - middle.bottom), 'docked to the bottom of .main mid-scroll').toBeLessThanOrEqual(1);
      await expectInsideViewport(page, approve, 'Approve mid-scroll');

      await page.locator('.main').evaluate((main) => { main.scrollTop = main.scrollHeight; });
      const footer = page.locator('.page-footer');
      await expect(footer).toBeInViewport();
      expect((await boxOf(bar)).bottom, 'the bar sits in flow above the footer').toBeLessThanOrEqual((await boxOf(footer)).top);
      await expectInsideViewport(page, approve, 'Approve at the end');
    });
  }

  test('focus that arrives from below the view stops above the bar (WCAG 2.4.11)', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const { bar, approve, reject } = decisionControls(page);
    await expect(approve).toBeEnabled();
    // The page's last control above the bar (the draft panel's Save draft).
    const saveDraft = page.getByRole('button', { name: `Save outreach draft for ${PRIMARY_BORROWER.borrower_id}` });
    // How far the scroll-margin at the end of a control outside the bar
    // falls short of the bar, in px (0 when it clears it): what focus
    // scrolling to the nearest edge uses.
    const shortfall = () => saveDraft.evaluate((node) => {
      const dock = document.querySelector<HTMLElement>('.offer-action-bar');
      if (!dock) return -1;
      const margin = Number.parseFloat(getComputedStyle(node).scrollMarginBlockEnd) || 0;
      return Math.max(0, dock.offsetHeight - margin);
    });
    await expect.poll(shortfall, { message: 'scroll-margin short of the bar (px)' }).toBe(0);

    // Save draft starts behind the bar. Scrolled to the nearest edge, as
    // focus navigation does in Firefox and WebKit, it stops above the bar;
    // Chromium's own Shift+Tab clears it too.
    expect((await boxOf(saveDraft)).bottom, 'precondition: Save draft starts below the bar top').toBeGreaterThan((await boxOf(bar)).top);
    await saveDraft.evaluate((node) => node.scrollIntoView({ block: 'nearest' }));
    expect((await boxOf(saveDraft)).bottom, 'nearest-edge scrolling clears the bar').toBeLessThanOrEqual((await boxOf(bar)).top);
    await page.locator('.main').evaluate((main) => { main.scrollTop = 0; });
    await page.locator('#lo-assign').focus();
    await page.keyboard.press('Shift+Tab');
    await expect(saveDraft).toBeFocused();
    expect((await boxOf(saveDraft)).bottom, 'the focused control clears the bar').toBeLessThanOrEqual((await boxOf(bar)).top);

    // The clearance follows the bar's measured size: the open reject
    // rationale grows it past the stylesheet's pre-measurement fallback.
    const fallback = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return Number.parseFloat(root.getPropertyValue('--sp-16')) * 2;
    });
    await reject.click();
    await expect.poll(async () => (await boxOf(bar)).bottom - (await boxOf(bar)).top).toBeGreaterThan(fallback);
    await expect.poll(shortfall, { message: 'scroll-margin short of the grown bar (px)' }).toBe(0);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: focus inside the docked bar never scrolls the page`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      const { bar, approve, reject, routing, followUp } = decisionControls(page);
      await expect(approve).toBeEnabled();
      const start = await mainScroll(page);
      expect(start.top, 'precondition: the page has not scrolled').toBe(0);
      // Non-vacuous: a focus scroll chasing the sticky bar has room to move
      // `.main` a long way (it used to land 425-596px down).
      expect(start.max, 'precondition: the page can scroll well past the bar').toBeGreaterThan(300);

      // Read after the frame that follows the focus change, so a scroll the
      // focus started has landed.
      const scrollTopAfter = async (step: string) => {
        await page.evaluate(() => new Promise<void>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
        }));
        const { top } = await mainScroll(page);
        expect(top, `${step}: .main scrollTop (px)`).toBeLessThanOrEqual(1);
        await expectInsideViewport(page, approve, `Approve after ${step}`);
      };

      // Pointer: the routing select opens its list and takes focus.
      await routing.click();
      await expect(routing).toBeFocused();
      await page.keyboard.press('Escape');
      await scrollTopAfter('clicking the routing select');

      // Keyboard and programmatic focus on the bar's controls.
      await page.keyboard.press('Tab');
      await expect(followUp).toBeFocused();
      await scrollTopAfter('tabbing to the follow-up select');
      await routing.focus();
      await scrollTopAfter('focusing the routing select from script');

      // Reject opens its rationale in the bar and focuses the reason select.
      await reject.click();
      const form = bar.getByRole('form', { name: 'Reject rationale' });
      await expect(form.getByRole('combobox', { name: /^Reason/ })).toBeFocused();
      await scrollTopAfter('Reject opening its rationale');
      // A text field reveals its caret on focus, a scroll path of its own.
      await page.keyboard.press('Tab');
      await expect(form.getByRole('textbox', { name: 'Rationale note' })).toBeFocused();
      await scrollTopAfter('tabbing to the rationale note');

      // Cancel closes the form and hands focus back to Reject.
      await form.getByRole('button', { name: 'Cancel' }).click();
      await expect(form).toHaveCount(0);
      await expect(reject).toBeFocused();
      await scrollTopAfter('Cancel returning focus to Reject');
    });
  }

  test('Reject opens its rationale inside the bar, focused, and Cancel closes it back onto Reject', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const { bar, reject } = decisionControls(page);
    await expect(reject).toBeEnabled();
    await reject.click();
    const form = bar.getByRole('form', { name: 'Reject rationale' });
    await expect(form).toBeVisible();
    await expect(form.getByRole('combobox', { name: /^Reason/ })).toBeFocused();
    await expectInsideViewport(page, form.getByRole('button', { name: 'Confirm reject' }), 'Confirm reject');
    // Keyboard Cancel: the form unmounts with focus inside it, which used to
    // drop focus to <body>. It returns to the Reject button that opened it.
    await form.getByRole('button', { name: 'Cancel' }).focus();
    await page.keyboard.press('Enter');
    await expect(form).toHaveCount(0);
    await expect(reject, 'focus returns to Reject, not <body>').toBeFocused();
  });

  test('the left column stacks alternatives and thresholds under the primary offer', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const surface = (title: string) => page.locator('.layoutA-grid .surface', { has: page.getByText(title, { exact: true }) }).first();
    const primarySurface = surface('Primary offer');
    const primary = await boxOf(primarySurface);
    const alternatives = await boxOf(surface('Considered alternatives'));
    const thresholds = await boxOf(surface('Thresholds applied'));
    const draft = await boxOf(surface('Governed outreach · exact audited copy'));
    for (const [name, box] of [['Considered alternatives', alternatives], ['Thresholds applied', thresholds]] as const) {
      expect(Math.abs(box.left - primary.left), `${name} shares the primary offer's column`).toBeLessThanOrEqual(1);
      expect(Math.abs(box.right - primary.right), `${name} spans the primary offer's column`).toBeLessThanOrEqual(1);
      expect(box.right, `${name} stays left of the draft`).toBeLessThan(draft.left);
    }
    expect(thresholds.top, 'thresholds follow the alternatives').toBeGreaterThan(alternatives.bottom);
    expect(alternatives.top, 'alternatives follow the primary offer').toBeGreaterThan(primary.bottom);
    expect(alternatives.top, 'the reasoning sits beside the draft, not in a row below it').toBeLessThan(draft.bottom);
    // No stretched card: the primary offer is as tall as its own header and
    // body (plus its borders), not the row height the draft sets.
    const slack = await primarySurface.evaluate((node) => {
      const own = node.getBoundingClientRect().height;
      const content = [...node.children].reduce((sum, child) => sum + child.getBoundingClientRect().height, 0);
      return own - content;
    });
    expect(slack, 'empty space inside the Primary offer card (px)').toBeLessThanOrEqual(2);
  });
});

interface Readability {
  /** Scroll positions at which some part of the element is painted and uncovered. */
  positions: number;
  /** Bands of the element's height never uncovered at any scroll position. */
  unseenBands: number;
  bands: number;
}

/**
 * Step `.main` from the top to the end, 16px at a time, and hit-test each
 * element down its centre line in 8px bands with `elementFromPoint`: a band
 * counts as readable when, at some scroll position, it is inside the
 * scrollport and the topmost thing painted there is the element itself (not
 * the sticky route nav, not the decision bar). Loss of content (WCAG 1.4.10)
 * is a band that is never readable.
 */
async function readabilityWhileScrolling(page: Page, targets: Locator[]): Promise<Readability[]> {
  const handles = await Promise.all(targets.map(async (target) => {
    const handle = await target.elementHandle();
    if (!handle) throw new Error('target is not attached');
    return handle;
  }));
  return page.evaluate((elements) => {
    const main = document.querySelector<HTMLElement>('.main');
    if (!main) throw new Error('no .main scroller');
    const STEP = 16;
    const BAND = 8;
    const max = main.scrollHeight - main.clientHeight;
    const offsets = elements.map((element) => {
      const height = element.getBoundingClientRect().height;
      const list: number[] = [];
      for (let offset = BAND / 2; offset < height; offset += BAND) list.push(offset);
      return list;
    });
    const seen = elements.map(() => new Set<number>());
    const positions = elements.map(() => 0);
    for (let top = 0; ; top = Math.min(top + STEP, max)) {
      main.scrollTo({ top, behavior: 'instant' });
      const view = main.getBoundingClientRect();
      const viewTop = view.top + main.clientTop;
      const viewBottom = viewTop + main.clientHeight;
      elements.forEach((element, index) => {
        const rect = element.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        let readableHere = false;
        offsets[index].forEach((offset, band) => {
          const y = rect.top + offset;
          if (y < viewTop || y >= viewBottom) return;
          const hit = document.elementFromPoint(x, y);
          if (hit && (hit === element || element.contains(hit))) {
            seen[index].add(band);
            readableHere = true;
          }
        });
        if (readableHere) positions[index] += 1;
      });
      if (top >= max) break;
    }
    main.scrollTo({ top: 0, behavior: 'instant' });
    return elements.map((_, index) => ({
      positions: positions[index],
      unseenBands: offsets[index].length - seen[index].size,
      bands: offsets[index].length,
    }));
  }, handles);
}

/**
 * The Primary offer card and every paragraph of the certified copy can each
 * be read, band by band, at some scroll position of `.main`; and at the end
 * of the page Approve is in view and uncovered, so the gate stays reachable.
 */
async function expectOfferAndCopyReadable(page: Page): Promise<void> {
  const { approve } = decisionControls(page);
  const paragraphs = page.locator('[data-testid="outreach-draft"] p');
  await expect(paragraphs.first()).toBeVisible();
  const paragraphCount = await paragraphs.count();
  expect(paragraphCount, 'precondition: the certified copy has several paragraphs').toBeGreaterThan(1);
  const primaryOffer = page.locator('.layoutA-grid .surface', { has: page.getByText('Primary offer', { exact: true }) }).first();
  await expect(primaryOffer).toBeVisible();

  const targets = [primaryOffer, ...Array.from({ length: paragraphCount }, (_, index) => paragraphs.nth(index))];
  const names = ['Primary offer', ...Array.from({ length: paragraphCount }, (_, index) => `copy paragraph ${index + 1}`)];
  const report = await readabilityWhileScrolling(page, targets);
  report.forEach((result, index) => {
    expect(result.bands, `precondition: ${names[index]} has height`).toBeGreaterThan(0);
    expect(result.positions, `${names[index]} is readable at some scroll position`).toBeGreaterThanOrEqual(1);
    expect(result.unseenBands, `${names[index]}: 8px bands never readable at any scroll position`).toBe(0);
  });

  await page.locator('.main').evaluate((main) => { main.scrollTo({ top: main.scrollHeight, behavior: 'instant' }); });
  await expectInsideViewport(page, approve, 'Approve at the end of the page');
}

/**
 * Browser zoom shrinks the CSS viewport: 1440x900 at 200% renders as 720x450,
 * 1280x720 at 150% as 853x480. There the docked bar (about 300px tall once
 * stacked) and the sticky route nav covered the whole `.main` scrollport, so
 * the Primary offer and the certified copy were readable at no scroll
 * position (WCAG 1.4.4 / 1.4.10). The bar docks only in a viewport at least
 * 40rem tall; below that it is in flow at the end of the page, as the gate
 * was before it docked. The same holds while the Console is a bottom sheet
 * over half the viewport (below 1280px wide).
 */
test.describe('decision bar in a short viewport (visual-v1, WCAG 1.4.10)', () => {
  const ZOOMED = [
    { label: '1440x900 at 200% zoom', width: 720, height: 450 },
    { label: '1280x720 at 150% zoom', width: 853, height: 480 },
  ] as const;

  for (const { label, width, height } of ZOOMED) {
    test(`${label} (${width}x${height}): the offer and every paragraph of the certified copy can be read`, async ({ app, page }) => {
      await page.setViewportSize({ width, height });
      await app.gotoRoute(ROUTE);
      await expect(decisionControls(page).approve).toBeEnabled();
      await expectOfferAndCopyReadable(page);
    });
  }

  test('1024x768 with the Console open as a bottom sheet: the offer and the copy can be read', async ({ app, page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await app.gotoRoute(ROUTE);
    await expect(decisionControls(page).approve).toBeEnabled();
    const sheet = await app.openConsole();
    const sheetBox = await boxOf(sheet);
    expect(sheetBox.top, 'precondition: below 1280px the Console is a bottom sheet').toBeGreaterThan(0);
    expect(sheetBox.right - sheetBox.left, 'precondition: the sheet spans the width').toBeGreaterThan(900);
    await expectOfferAndCopyReadable(page);
  });

  test('1366x768 at 100% keeps the bar docked: Approve on screen without scrolling', async ({ app, page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await app.gotoRoute(ROUTE);
    const { bar, approve, routing } = decisionControls(page);
    await expect(approve).toBeEnabled();
    const { top, max, bottom } = await mainScroll(page);
    expect(top, 'precondition: the page has not scrolled').toBe(0);
    expect(max, 'precondition: the page is taller than the viewport').toBeGreaterThan(0);
    await expectInsideViewport(page, approve, 'Approve');
    await expectInsideViewport(page, routing, 'routing selector');
    expect(Math.abs((await boxOf(bar)).bottom - bottom), 'docked to the bottom of .main').toBeLessThanOrEqual(1);
  });
});

/** dt label -> dd text of the frame header, in document order. */
async function frameHeader(frame: Locator): Promise<Array<[string, string]>> {
  return frame.locator('dl > div').evaluateAll((rows) =>
    rows.map((row): [string, string] => [row.querySelector('dt')?.textContent ?? '', row.querySelector('dd')?.textContent ?? '']),
  );
}

async function axeViolations(page: Page, selector: string): Promise<string[]> {
  const results = await new AxeBuilder({ page }).include(selector).withTags(WCAG_TAGS).analyze();
  return results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`);
}

test.describe('certified copy preview (critic-02)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the email frame shows From, the synthetic To, a wrapping subject and exactly the audited body`, async ({ app, mockApi, page }) => {
      const served = registerDraftCopy(mockApi, { subject: LONG_SUBJECT, emailBody: EMAIL_BODY });
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      const frame = page.getByRole('article', { name: 'Certified email preview' });
      await expect(frame).toBeVisible();
      expect(await frameHeader(frame)).toEqual([
        ['From', LENDER_NAME],
        ['To', `${PRIMARY_BORROWER.borrower_id}Synthetic contact`],
        ['Subject', LONG_SUBJECT],
      ]);

      // The subject wraps inside the frame: every character is painted, on
      // more than one line, with nothing scrolled or cut off.
      const subject = frame.getByTestId('outreach-subject');
      await expect(subject).toHaveAttribute('aria-label', 'Outreach subject — review only');
      const subjectMetrics = await subject.evaluate((node) => ({
        scrollWidth: node.scrollWidth,
        clientWidth: node.clientWidth,
        lines: Math.round(node.getBoundingClientRect().height / Number.parseFloat(getComputedStyle(node).lineHeight)),
      }));
      expect(subjectMetrics.scrollWidth).toBeLessThanOrEqual(subjectMetrics.clientWidth);
      expect(subjectMetrics.lines, 'the long subject wraps').toBeGreaterThan(1);
      expect((await boxOf(subject)).right).toBeLessThanOrEqual((await boxOf(frame)).right);

      // The painted body is the audited copy, character for character: its
      // paragraphs render as blocks, so innerText restores the blank lines,
      // and the sign-off's single line break paints as a break.
      const audited = served.email?.body ?? '';
      expect(audited, 'precondition: the served draft is the lane body').toBe(EMAIL_BODY);
      expect(audited.split('\n\n').length, 'precondition: the draft has several paragraphs').toBeGreaterThan(1);
      expect(audited.split('\n\n').some((paragraph) => paragraph.includes('\n')), 'precondition: a line break inside a paragraph').toBe(true);
      const body = frame.getByTestId('outreach-draft');
      await expect(body).toHaveAttribute('aria-label', 'Outreach draft — review only');
      await expect(body.locator('p')).toHaveCount(audited.split('\n\n').length);
      expect(await body.innerText()).toBe(audited);
      const frameOverflow = await frame.evaluate((node) => node.scrollWidth - node.clientWidth);
      expect(frameOverflow, 'nothing in the frame overflows sideways').toBeLessThanOrEqual(0);

      await expect(frame.locator('footer')).toHaveText(/Disclosure fixture-2026-07 · IL/);
      await expect(page.getByText('Governed outreach · exact audited copy')).toBeVisible();
      await expect(frame.locator('input, textarea, [contenteditable="true"]')).toHaveCount(0);
      expect(await axeViolations(page, '[data-testid="certified-copy"]')).toEqual([]);
    });

    test(`${theme}: SMS renders as a bubble with its carrier segment count`, async ({ app, mockApi, page }) => {
      registerDraftCopy(mockApi, { smsBody: GSM_SMS_BODY });
      await app.setTheme(theme);
      await app.gotoRoute(ROUTE);
      await page.getByRole('button', { name: 'SMS', exact: true }).click();
      const frame = page.getByRole('article', { name: 'Certified SMS preview' });
      await expect(frame).toBeVisible();
      await expect(frame.getByTestId('outreach-draft')).toHaveText(GSM_SMS_BODY);
      expect((await frameHeader(frame)).map(([term]) => term)).toEqual(['From', 'To']);
      await expect(frame.getByTestId('outreach-subject')).toHaveCount(0);

      const bubble = frame.getByTestId('outreach-draft');
      expect(await bubble.innerText()).toBe(GSM_SMS_BODY);
      // Outbound: the bubble sits at the thread's end, as the phone shows it.
      const thread = await boxOf(frame.locator('.certified-copy__thread'));
      const bubbleBox = await boxOf(bubble);
      expect(thread.right - bubbleBox.right).toBeLessThan(bubbleBox.left - thread.left);
      await expect(frame.getByTestId('sms-segments')).toHaveText(`1 segment · ${GSM_SMS_BODY.length} of 160 characters · GSM-7`);
      expect(await axeViolations(page, '[data-testid="certified-copy"]')).toEqual([]);
    });
  }

  test('one non-GSM character switches the SMS count to UCS-2 segments', async ({ app, mockApi, page }) => {
    registerDraftCopy(mockApi, { smsBody: UCS2_SMS_BODY });
    await app.gotoRoute(ROUTE);
    await page.getByRole('button', { name: 'SMS', exact: true }).click();
    const frame = page.getByRole('article', { name: 'Certified SMS preview' });
    await expect(frame.getByTestId('outreach-draft')).toHaveText(UCS2_SMS_BODY);
    await expect(frame.getByTestId('sms-segments')).toHaveText(
      `2 segments · ${UCS2_SMS_BODY.length} characters at 67 per segment · UCS-2 (non-GSM characters)`,
    );
  });
});
