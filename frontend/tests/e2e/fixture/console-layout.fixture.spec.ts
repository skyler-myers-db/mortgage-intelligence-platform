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
