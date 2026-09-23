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
 * Console, not the Genie launcher); scrolling keeps the bar docked; at the end
 * of the page it rests above the footer; focus from below the view stops
 * above it; the reject rationale opens inside it, focused.
 */
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const ROUTE = `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`;

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

/** The Genie launchers: the topbar toggle at every width, and the FAB where it is displayed. */
async function genieLauncherBoxes(page: Page): Promise<Box[]> {
  const boxes = [await boxOf(page.getByRole('banner').getByRole('button', { name: 'Toggle Genie chat' }))];
  const fab = page.locator('.genie__fab');
  if (await fab.isVisible()) boxes.push(await boxOf(fab));
  return boxes;
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

      const launchers = await genieLauncherBoxes(page);
      for (const [name, control] of [['Approve', approve], ['Reject', reject], ['routing selector', routing], ['follow-up selector', followUp]] as const) {
        await expectInsideViewport(page, control, name);
        const box = await boxOf(control);
        for (const launcher of launchers) {
          expect(overlaps(box, launcher), `${name} overlaps a Genie launcher`).toBe(false);
        }
      }
      // Docked: the bar ends on `.main`'s bottom edge while the page is above its end.
      expect(Math.abs((await boxOf(bar)).bottom - (await mainScroll(page)).bottom)).toBeLessThanOrEqual(1);

      // The hero carries no Approve any more: the only one is the gate's, beside the routing.
      await expect(page.locator('.proto-hero__actions').getByRole('button', { name: /approve/i })).toHaveCount(0);
      await expect(page.locator('#main-content').getByRole('button', { name: /^Approve/ })).toHaveCount(1);
    });
  }

  test('with the Console open the bar stays clear of it and in view', async ({ app, page }) => {
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

  test('where the floating Genie launcher shows, Approve stays clear of it', async ({ app, page }) => {
    await page.setViewportSize({ width: 700, height: 900 });
    await app.gotoRoute(ROUTE);
    const fab = page.locator('.genie__fab');
    await expect(fab, 'precondition: the FAB is displayed below 721px').toBeVisible();
    const { approve, routing } = decisionControls(page);
    await expect(approve).toBeEnabled();
    for (const [name, control] of [['Approve', approve], ['routing selector', routing]] as const) {
      await expectInsideViewport(page, control, name);
      expect(overlaps(await boxOf(control), await boxOf(fab)), `${name} overlaps the Genie FAB`).toBe(false);
    }
  });

  test('scrolling keeps the bar docked, and at the end it rests above the footer', async ({ app, page }) => {
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

  test('focus that arrives from below the view stops above the bar (WCAG 2.4.11)', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const { bar, approve, reject } = decisionControls(page);
    await expect(approve).toBeEnabled();
    // How far `.main`'s scroll-padding falls short of the bar, in px (0 when
    // it clears it): what focus scrolling to the nearest edge uses.
    const shortfall = () => page.evaluate(() => {
      const main = document.querySelector<HTMLElement>('.main');
      const dock = document.querySelector<HTMLElement>('.offer-action-bar');
      if (!main || !dock) return -1;
      // `auto` (no clearance) reads as 0.
      const padding = Number.parseFloat(getComputedStyle(main).scrollPaddingBlockEnd) || 0;
      return Math.max(0, dock.offsetHeight - padding);
    });
    await expect.poll(shortfall, { message: 'scroll-padding short of the bar (px)' }).toBe(0);

    // The page's last control above the bar (the draft panel's Save draft)
    // starts behind it. Scrolled to the nearest edge, as focus navigation
    // does in Firefox and WebKit, it stops above the bar; Chromium's own
    // Shift+Tab (which centres) clears it too.
    const saveDraft = page.getByRole('button', { name: `Save outreach draft for ${PRIMARY_BORROWER.borrower_id}` });
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
    await expect.poll(shortfall, { message: 'scroll-padding short of the grown bar (px)' }).toBe(0);
  });

  test('Reject opens its rationale inside the bar, focused, and Cancel closes it', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const { bar, reject } = decisionControls(page);
    await expect(reject).toBeEnabled();
    await reject.click();
    const form = bar.getByRole('form', { name: 'Reject rationale' });
    await expect(form).toBeVisible();
    await expect(form.getByRole('combobox', { name: /^Reason/ })).toBeFocused();
    await expectInsideViewport(page, form.getByRole('button', { name: 'Confirm reject' }), 'Confirm reject');
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toHaveCount(0);
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
