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

interface RunningTransition {
  property: string;
  playState: string;
  duration: number;
  delay: number;
}

/**
 * Close a panel from inside the page and read its state right after React
 * has flushed the click (one macrotask), then again once every running
 * transition on the panel has finished. Sampling in-page keeps the first
 * read well inside the exit window even on a loaded runner.
 */
async function closeAndSampleExit(page: Page, panelSelector: string, closeLabel: string) {
  return page.evaluate(async ([selector, label]) => {
    const panel = document.querySelector<HTMLElement>(selector);
    const close = panel?.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`);
    if (!panel || !close) throw new Error(`no ${selector} panel with a "${label}" control`);
    const read = () => {
      const style = getComputedStyle(panel);
      return { visibility: style.visibility, transform: style.transform, open: panel.classList.contains('is-open') };
    };
    close.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const justClosed = read();
    const running: RunningTransition[] = panel
      .getAnimations()
      .filter((animation): animation is CSSTransition => animation instanceof CSSTransition)
      .map((transition) => ({
        property: transition.transitionProperty,
        playState: transition.playState,
        duration: Number(transition.effect?.getTiming().duration ?? 0),
        delay: Number(transition.effect?.getTiming().delay ?? 0),
      }));
    const pending = panel.getAnimations().map((animation) => animation.finished.catch(() => undefined));
    await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));
    const nextFrame = read();
    await Promise.all(pending);
    const afterExit = read();
    return { justClosed, running, nextFrame, afterExit };
  }, [panelSelector, closeLabel] as const);
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

/** Four cards on at most two rows, and no row holding a single card: the
 * 3+1 layout also has two distinct tops, so counting tops alone is vacuous. */
async function expectNoOrphanRow(page: Page): Promise<void> {
  const rows = new Map<number, number>();
  for (const top of await kpiRowTops(page)) rows.set(top, (rows.get(top) ?? 0) + 1);
  expect(rows.size, 'four KPIs occupy at most two rows').toBeLessThanOrEqual(2);
  for (const [top, count] of rows) expect(count, `row at ${top}px holds more than one card`).toBeGreaterThanOrEqual(2);
}

test.describe('KPI band never orphans a card', () => {
  test('Home with the Console open lays four KPIs out without a lone card', async ({ app, page }) => {
    await app.gotoRoute('/');
    await app.openConsole();
    await expectNoOrphanRow(page);
  });

  test('a 1280-wide laptop without the Console shows four KPIs as 2x2', async ({ app, page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await app.gotoRoute('/');
    await expectNoOrphanRow(page);
  });
});

test.describe('overlay exits animate', () => {
  // The harness runs under prefers-reduced-motion: reduce; these cases
  // need the real motion contract. Playwright's actionability wait stalls on
  // rAF-driven reveals under heavy machine load, so give them the slow budget
  // rather than a retry.
  test.use({ contextOptions: { reducedMotion: 'no-preference' } });
  test.slow();

  test('the evidence drawer stays visible while it slides out and hides when the exit ends', async ({ app, page }) => {
    await app.gotoRoute('/');
    await page.locator('.kpi .evidence-chip').first().click();
    const drawer = page.locator('aside.drawer');
    await expect(drawer).toHaveClass(/is-open/);
    await expect(drawer).toBeVisible();

    const exit = await closeAndSampleExit(page, 'aside.drawer', 'Close drawer');
    expect(exit.justClosed.open).toBe(false);
    expect(exit.justClosed.visibility, 'mid-exit the drawer is still visible').toBe('visible');
    const slide = exit.running.find((t) => t.property === 'transform');
    expect(slide?.playState, 'the slide-out is running').toBe('running');
    expect(slide?.duration).toBeGreaterThan(0);
    const visibilityHold = exit.running.find((t) => t.property === 'visibility');
    expect(visibilityHold, 'visibility flips only after the slide-out').toBeDefined();
    expect(visibilityHold?.delay ?? 0).toBeGreaterThanOrEqual(slide?.duration ?? Number.POSITIVE_INFINITY);
    expect(exit.afterExit.visibility).toBe('hidden');
    await expect(drawer).toBeHidden();
  });

  test('the Genie panel fades out before it hides', async ({ app, page }) => {
    await app.gotoRoute('/');
    await app.openGenie();
    const exit = await closeAndSampleExit(page, '.genie', 'Close Genie');
    expect(exit.justClosed.open).toBe(false);
    expect(exit.justClosed.visibility).toBe('visible');
    expect(exit.running.some((t) => t.property === 'opacity' && t.playState === 'running')).toBe(true);
    expect(exit.running.find((t) => t.property === 'visibility')?.delay ?? 0).toBeGreaterThan(0);
    expect(exit.afterExit.visibility).toBe('hidden');
  });

  test('the Console rail transitions in when it opens', async ({ app, page }) => {
    await app.gotoRoute('/');
    const entering = await page.evaluate(async () => {
      const toggle = document.querySelector<HTMLButtonElement>('header [aria-label="Toggle console"]');
      if (!toggle) throw new Error('no Console toggle in the topbar');
      toggle.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      const panel = document.querySelector<HTMLElement>('aside.tweaks.is-open');
      if (!panel) throw new Error('no open Console panel after the toggle');
      return {
        opacity: getComputedStyle(panel).opacity,
        transitions: panel
          .getAnimations()
          .filter((animation): animation is CSSTransition => animation instanceof CSSTransition)
          .map((transition) => transition.transitionProperty),
      };
    });
    expect(entering.transitions, 'an opacity entry transition is running').toContain('opacity');
    expect(Number(entering.opacity), 'the panel starts from its @starting-style').toBeLessThan(1);
    await expect(page.getByRole('complementary', { name: 'Workspace console' }).locator('.tweaks__body')).toBeVisible();
    await expect(page.locator('aside.tweaks.is-open')).toHaveCSS('opacity', '1');
  });
});

test.describe('overlay exits are instant under reduced motion', () => {
  // Under reduced motion the panels drop `visibility` from their transition
  // list (a pending 0.01ms transition would still hold the visible side until
  // a frame commits), so the flip lands in the same style recalc as the close.
  const instant = (exit: Awaited<ReturnType<typeof closeAndSampleExit>>, label: string) => {
    expect(exit.justClosed.open, `${label} closed`).toBe(false);
    expect(exit.justClosed.visibility, `${label} hidden in the closing recalc`).toBe('hidden');
    expect(exit.running.map((transition) => transition.property), `${label} has no visibility transition`).not.toContain('visibility');
    for (const transition of exit.running) {
      expect(transition.delay, `${label} ${transition.property} has no delay`).toBe(0);
      expect(transition.duration, `${label} ${transition.property} is instant`).toBeLessThanOrEqual(1);
    }
    expect(exit.nextFrame.visibility).toBe('hidden');
    expect(exit.afterExit.visibility).toBe('hidden');
  };

  test('the evidence drawer and the Genie panel hide immediately when closed', async ({ app, page }) => {
    await app.gotoRoute('/');
    await page.locator('.kpi .evidence-chip').first().click();
    await expect(page.locator('aside.drawer')).toHaveClass(/is-open/);
    instant(await closeAndSampleExit(page, 'aside.drawer', 'Close drawer'), 'drawer');

    await app.openGenie();
    instant(await closeAndSampleExit(page, '.genie', 'Close Genie'), 'Genie');
  });
});

test.describe('pressed states', () => {
  test('a held button, nav chip and Console control show the shared pressed offset', async ({ app, page }) => {
    await app.gotoRoute('/');
    const panel = await app.openConsole();
    // Targets whose click keeps the page where it is (Home stays Home).
    const targets: Array<[string, Locator]> = [
      ['button', panel.getByRole('button', { name: 'Refresh' })],
      ['nav chip', page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Home' })],
      ['Console segmented control', panel.getByRole('button', { name: 'Compact' })],
    ];
    // The Console's recent-activity query refetches on mount and disables
    // Refresh meanwhile; a disabled button has no pressed state by design.
    await app.settle();
    for (const [label, target] of targets) {
      await expect(target).toBeEnabled();
      await target.scrollIntoViewIfNeeded();
      const box = await boxOf(target);
      await expect(target).toHaveCSS('translate', 'none');
      await page.mouse.move((box.left + box.right) / 2, (box.top + box.bottom) / 2);
      await page.mouse.down();
      await expect(target, `${label} nudges down while held`).toHaveCSS('translate', '0px 1px');
      await page.mouse.up();
      await expect(target, `${label} settles back on release`).toHaveCSS('translate', 'none');
    }
  });
});

test.describe('route navigation links', () => {
  test('nav labels render without the browser underline and still show hover and focus', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const links = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link');
    const count = await links.count();
    expect(count).toBeGreaterThan(3);
    for (let index = 0; index < count; index += 1) {
      await expect(links.nth(index)).toHaveCSS('text-decoration-line', 'none');
    }
    const idle = links.filter({ hasText: 'Home' });
    const before = await idle.evaluate((link) => getComputedStyle(link).borderColor);
    await idle.hover();
    await expect.poll(() => idle.evaluate((link) => getComputedStyle(link).borderColor), 'hover changes the chip border').not.toBe(before);
    await expect(idle).toHaveCSS('text-decoration-line', 'none');
    await idle.focus();
    await expect(idle).toBeFocused();
    await expect(idle, 'keyboard focus draws the ring, not an underline').toHaveCSS('outline-style', 'solid');
    await expect(idle).toHaveCSS('text-decoration-line', 'none');
  });
});
