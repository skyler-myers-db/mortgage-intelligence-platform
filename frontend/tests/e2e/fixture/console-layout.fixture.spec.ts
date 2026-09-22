/**
 * Console rail, overlay exits, KPI band and nav links — rendered-layer proofs
 * for the 2026-09-21 audit lane "console-motion" (visual-02 / shell-02 /
 * responsive-01, responsive-v1, responsive-06, css-03 / motion-01,
 * visual-05). Every assertion here reads geometry or computed style from the
 * production build; nothing is pinned as a known defect.
 */
import type { Locator, Page } from '@playwright/test';
import { expect, test } from './test';

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface ControlBox extends Box {
  name: string;
}

const CONTROL_SELECTOR = 'button, input, select, textarea, a[href]';

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no bounding box');
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

/**
 * Each control inside `.tweaks__body`, scrolled into view (the body scrolls
 * vertically by design) and measured against the panel's own box.
 */
async function consoleControls(panel: Locator): Promise<{ panel: Box; controls: ControlBox[] }> {
  return panel.evaluate((element, selector) => {
    const rect = element.getBoundingClientRect();
    const panelBox = { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
    const controls: ControlBox[] = [];
    for (const control of element.querySelectorAll<HTMLElement>(`.tweak-row ${selector}`)) {
      control.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      const r = control.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      controls.push({
        name: control.getAttribute('aria-label') ?? control.textContent?.trim() ?? control.tagName,
        left: r.left,
        top: r.top,
        right: r.right,
        bottom: r.bottom,
      });
    }
    return { panel: panelBox, controls };
  }, CONTROL_SELECTOR);
}

async function bodyOverflow(panel: Locator): Promise<{ scrollWidth: number; clientWidth: number }> {
  return panel.locator('.tweaks__body').evaluate((body) => ({
    scrollWidth: body.scrollWidth,
    clientWidth: body.clientWidth,
  }));
}

test.describe('Console rail fits its own panel', () => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }]) {
    for (const route of ['/', '/lead-queue']) {
      test(`${route} at ${viewport.width}x${viewport.height}: every Console control stays inside the 300px panel`, async ({ app, page }) => {
        await page.setViewportSize(viewport);
        await app.gotoRoute(route);
        const panel = await app.openConsole();

        const overflow = await bodyOverflow(panel);
        expect(overflow.scrollWidth, 'the Console body must not scroll horizontally').toBeLessThanOrEqual(overflow.clientWidth);

        const { panel: panelBox, controls } = await consoleControls(panel);
        expect(controls.length).toBeGreaterThan(8);
        for (const control of controls) {
          expect(control.left, `${control.name} starts inside the panel`).toBeGreaterThanOrEqual(panelBox.left);
          expect(control.right, `${control.name} ends inside the panel`).toBeLessThanOrEqual(panelBox.right);
          expect(control.top, `${control.name} top inside the panel`).toBeGreaterThanOrEqual(panelBox.top);
          expect(control.bottom, `${control.name} bottom inside the panel`).toBeLessThanOrEqual(panelBox.bottom);
        }

        const light = panel.getByRole('button', { name: 'Light' });
        const compact = panel.getByRole('button', { name: 'Compact' });
        await expect(light).toBeVisible();
        await expect(compact).toBeVisible();
        await light.click();
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
        await compact.click();
        await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
      });
    }
  }

  test('the Console labels stop restyling the embedded property-lookup form', async ({ app }) => {
    await app.gotoRoute('/');
    const panel = await app.openConsole();
    const rowLabel = panel.locator('.tweak-row > label').first();
    await expect(rowLabel).toHaveCSS('text-transform', 'uppercase');
    const fieldLabel = panel.locator('.property-lookup form label').first();
    await expect(fieldLabel).toHaveCSS('text-transform', 'none');
  });
});

test.describe('Console and Genie share the right edge without colliding', () => {
  test('opening the Console then Genie keeps both panels apart and on screen', async ({ app, page }) => {
    await app.gotoRoute('/');
    const consolePanel = await app.openConsole();
    const genie = await app.openGenie();

    const consoleBox = await boxOf(consolePanel);
    const genieBox = await boxOf(genie);
    expect(overlaps(consoleBox, genieBox), 'the Genie panel must not sit under the Console').toBe(false);

    const viewport = page.viewportSize();
    if (!viewport) throw new Error('viewport size is unset');
    expect(genieBox.left).toBeGreaterThanOrEqual(0);
    expect(genieBox.right).toBeLessThanOrEqual(viewport.width);
    expect(genieBox.top).toBeGreaterThanOrEqual(0);
    expect(genieBox.bottom).toBeLessThanOrEqual(viewport.height);

    // Interactive controls of each panel stay clear of the other panel.
    const askBox = await boxOf(genie.getByRole('button', { name: 'Ask' }));
    const inputBox = await boxOf(genie.getByRole('textbox', { name: 'Ask Genie' }));
    const lightBox = await boxOf(consolePanel.getByRole('button', { name: 'Light' }));
    expect(overlaps(askBox, consoleBox)).toBe(false);
    expect(overlaps(inputBox, consoleBox)).toBe(false);
    expect(overlaps(lightBox, genieBox)).toBe(false);

    // Closing the Console returns the docked panel to its right-edge anchor.
    await consolePanel.getByRole('button', { name: 'Close console' }).click();
    await expect.poll(async () => (await boxOf(genie)).right).toBeGreaterThan(genieBox.right);
  });

  test('a Genie panel the user dragged keeps its own position', async ({ app, page }) => {
    await app.gotoRoute('/');
    const genie = await app.openGenie();
    const header = genie.locator('.genie__hdr');
    const start = await boxOf(header);
    await page.mouse.move(start.left + 8, start.top + 8);
    await page.mouse.down();
    await page.mouse.move(start.left - 300, start.top - 200, { steps: 6 });
    await page.mouse.up();
    await expect(genie).toHaveClass(/is-undocked/);
    const dragged = await boxOf(genie);
    await app.openConsole();
    expect(await boxOf(genie)).toEqual(dragged);
  });
});

async function kpiRowTops(page: Page): Promise<number[]> {
  const cards = page.locator('.kpi-row > .kpi');
  await expect(cards).toHaveCount(4);
  return cards.evaluateAll((elements) => elements.map((element) => Math.round(element.getBoundingClientRect().top)));
}

test.describe('KPI band never orphans a card', () => {
  test('Home with the Console open lays four KPIs out without a lone card', async ({ app, page }) => {
    await app.gotoRoute('/');
    await app.openConsole();
    const rows = new Map<number, number>();
    for (const top of await kpiRowTops(page)) rows.set(top, (rows.get(top) ?? 0) + 1);
    expect(rows.size, 'four KPIs occupy at most two rows').toBeLessThanOrEqual(2);
    for (const [top, count] of rows) expect(count, `row at ${top}px holds more than one card`).toBeGreaterThanOrEqual(2);
  });

  test('a 1280-wide laptop without the Console shows four KPIs as 2x2', async ({ app, page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await app.gotoRoute('/');
    const tops = await kpiRowTops(page);
    expect(new Set(tops).size).toBe(2);
  });
});
