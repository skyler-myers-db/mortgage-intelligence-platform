/**
 * Lane filter-listbox (audit 2026-09-21 a11y-02 / stack-05 / tables-06 /
 * shell-07), proven in the production build:
 *
 *  - a Lead Queue filter is an APG select-only combobox: it opens from the
 *    keyboard, names its active option through aria-activedescendant,
 *    typeahead picks an option, Enter writes the URL param, Escape returns
 *    focus, and the active option wears the shared focus ring;
 *  - a filter opened near the bottom edge of the viewport opens upward, and
 *    stays fully visible;
 *  - the Analytics view tabs switch with the arrow keys;
 *  - the topbar borrower search results are reachable and openable with the
 *    keyboard alone.
 */
import type { Locator, Page } from '@playwright/test';
import { asComputedRgb, tokenValue } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

function stateFilter(page: Page): Locator {
  return page.locator('button[aria-haspopup="listbox"][aria-label^="STATE:"]').first();
}

/** The element the focus owner's aria-activedescendant names. */
async function activeDescendant(page: Page, owner: Locator): Promise<Locator> {
  const id = await owner.getAttribute('aria-activedescendant');
  expect(id, 'the focus owner names an active option').toBeTruthy();
  return page.locator(`[id="${id}"]`);
}

/** The active option paints the shared ring: --focus-ring-width solid --focus-ring-color. */
async function expectFocusRing(page: Page, option: Locator): Promise<void> {
  const ring = await option.evaluate((node) => {
    const style = getComputedStyle(node);
    return { style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor };
  });
  expect(ring.style).toBe('solid');
  expect(ring.width).toBe(await tokenValue(option, '--focus-ring-width'));
  expect(ring.color).toBe(await asComputedRgb(page, await tokenValue(option, '--focus-ring-color')));
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test('a Lead Queue filter is a keyboard combobox: typeahead picks, Enter writes ?state=, Escape returns focus', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const trigger = stateFilter(page);
      await trigger.focus();
      await expect(trigger).toHaveAttribute('role', 'combobox');
      await expect(trigger).toHaveAttribute('aria-expanded', 'false');

      await page.keyboard.press('ArrowDown');
      const listbox = page.getByRole('listbox', { name: 'STATE' });
      await expect(listbox).toBeVisible();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await expect(trigger).toHaveAttribute('aria-controls', (await listbox.getAttribute('id')) ?? 'missing');
      await expect(trigger, 'focus stays on the combobox').toBeFocused();
      await expect(await activeDescendant(page, trigger)).toHaveText('All states');

      // Typeahead: "c" lands on CA, "o" within the buffer window refines to CO.
      await page.keyboard.type('co');
      const active = await activeDescendant(page, trigger);
      await expect(active).toHaveText('CO');
      await expect(active).toHaveAttribute('role', 'option');
      await expectFocusRing(page, active);

      await page.keyboard.press('Enter');
      await expect(listbox).toHaveCount(0);
      await expect(page).toHaveURL(/[?&]state=CO(&|$)/);
      await expect(trigger).toHaveAttribute('aria-label', 'STATE: CO');
      await expect(trigger).toBeFocused();
      await app.settle();

      await page.keyboard.press('ArrowDown');
      await expect(listbox).toBeVisible();
      await expect(await activeDescendant(page, trigger)).toHaveText('CO');
      await page.keyboard.press('Escape');
      await expect(listbox).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await expect(page).toHaveURL(/[?&]state=CO(&|$)/);
    });

    test('a filter opened near the bottom edge of the viewport opens upward and stays in view', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      // APPROVAL, a short menu in the core pill row: since the wave-1b queue
      // layout moved that row up to y~258, the 280px state menu no longer fits
      // ABOVE its trigger either, so it cannot prove the flip.
      const trigger = page.locator('button[aria-haspopup="listbox"][aria-label^="APPROVAL:"]').first();

      // Control at 1440x900: there is room below, so the menu opens downward.
      const menu = await app.openFilterMenu('APPROVAL');
      const roomy = { trigger: await trigger.boundingBox(), menu: await menu.boundingBox() };
      if (!roomy.trigger || !roomy.menu) throw new Error('filter not laid out');
      expect(roomy.menu.y).toBeGreaterThanOrEqual(roomy.trigger.y + roomy.trigger.height);
      await expect(menu).not.toHaveClass(/filter-menu--up/);
      const menuHeight = roomy.menu.height;
      await page.keyboard.press('Escape');
      await expect(menu).toHaveCount(0);

      // Shrink the viewport until the trigger sits just above the bottom edge.
      await page.setViewportSize({ width: 1440, height: Math.ceil(roomy.trigger.y + roomy.trigger.height + 60) });
      const cramped = await trigger.boundingBox();
      if (!cramped) throw new Error('filter not laid out after resize');
      const viewportHeight = page.viewportSize()?.height ?? 0;
      const spaceBelow = viewportHeight - (cramped.y + cramped.height);
      // Non-vacuity: the menu really does not fit below, and fits above.
      expect(spaceBelow).toBeLessThan(menuHeight);
      expect(cramped.y).toBeGreaterThan(menuHeight);

      await trigger.focus();
      await page.keyboard.press('ArrowDown');
      const flipped = page.getByRole('listbox', { name: 'APPROVAL' });
      await expect(flipped).toBeVisible();
      await expect(flipped).toHaveClass(/filter-menu--up/);
      const box = await flipped.boundingBox();
      if (!box) throw new Error('menu not laid out');
      expect(box.y + box.height).toBeLessThanOrEqual(cramped.y + 1);
      expect(box.y).toBeGreaterThanOrEqual(0);
      // Nothing covers or clips it: the first option is the hit target at its centre.
      const first = flipped.getByRole('option').first();
      const hit = await first.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return top !== null && node.contains(top);
      });
      expect(hit, 'the flipped menu is on top and unclipped').toBe(true);
    });

    test('topbar search results are reachable and openable with the keyboard', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      const search = page.getByRole('banner').getByRole('combobox', { name: 'Search borrowers' });
      // The "/" shortcut focuses the search.
      await page.keyboard.press('/');
      await expect(search).toBeFocused();
      await page.keyboard.type('B-');
      const results = page.getByRole('listbox', { name: 'Borrower matches' });
      await expect(results).toBeVisible();
      await expect(search).toHaveAttribute('aria-expanded', 'true');
      await expect(search).toHaveAttribute('aria-controls', (await results.getAttribute('id')) ?? 'missing');
      await expect(search).not.toHaveAttribute('aria-activedescendant', /.+/);

      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('ArrowDown');
      const second = results.getByRole('option').nth(1);
      await expect(second).toHaveAttribute('aria-selected', 'true');
      await expect(search).toHaveAttribute('aria-activedescendant', (await second.getAttribute('id')) ?? 'missing');
      await expect(search, 'focus stays in the input').toBeFocused();
      await expectFocusRing(page, second);
      const borrowerId = (await second.locator('.mono').textContent())?.trim() ?? '';
      expect(borrowerId).toMatch(/^B-[0-9A-Z]{13}$/);

      // Escape closes the results but keeps the query; a second Escape clears it.
      await page.keyboard.press('Escape');
      await expect(results).toHaveCount(0);
      await expect(search).toHaveValue('B-');
      await expect(search).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(search).toHaveValue('');

      await page.keyboard.type('B-');
      await expect(results).toBeVisible();
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(new RegExp(`/borrower-360/${borrowerId}$`));
      await app.settle();
    });
  });
}

test('a pointer pick still selects and closes the filter menu', async ({ app, page }) => {
  await app.gotoRoute('/lead-queue');
  const menu = await app.openFilterMenu('STATE');
  await menu.getByRole('option', { name: 'TX', exact: true }).click();
  await expect(menu).toHaveCount(0);
  await expect(page).toHaveURL(/[?&]state=TX(&|$)/);
  await expect(stateFilter(page)).toHaveAttribute('aria-label', 'STATE: TX');
  await app.settle();
});

/**
 * Every block in the Analytics tabpanel spans the page column edge to edge,
 * like the tablist above it (the panel is a plain wrapper; the
 * `.main__inner > .surface` rules it steps between must not have mattered).
 */
async function expectPanelFlushWithTabs(page: Page): Promise<void> {
  const edges = await page.evaluate(() => {
    const box = (node: Element) => {
      const rect = node.getBoundingClientRect();
      return { left: rect.left, right: rect.right };
    };
    const tabs = document.querySelector('.analytics-tabs');
    const panel = document.querySelector('[role="tabpanel"]');
    return {
      tabs: tabs ? box(tabs) : null,
      blocks: [...(panel?.children ?? [])].map((node) => ({ name: node.className, ...box(node) })),
    };
  });
  expect(edges.tabs).not.toBeNull();
  expect(edges.blocks.length).toBeGreaterThan(0);
  for (const block of edges.blocks) {
    expect(block.left, `${block.name} left edge`).toBeCloseTo(edges.tabs?.left ?? -1, 0);
    expect(block.right, `${block.name} right edge`).toBeCloseTo(edges.tabs?.right ?? -1, 0);
  }
}

test('Analytics view tabs switch with the arrow keys', async ({ app, page }) => {
  await app.gotoRoute('/analytics');
  const tablist = page.getByRole('tablist', { name: 'Analytics views' });
  const executive = tablist.getByRole('tab', { name: 'Executive' });
  await expect(executive).toHaveAttribute('tabindex', '0');
  await expect(tablist.getByRole('tab', { name: 'Geography' })).toHaveAttribute('tabindex', '-1');

  // The tabpanel wraps the filters and the view; it must not move them.
  await expectPanelFlushWithTabs(page);
  await executive.focus();

  await page.keyboard.press('ArrowRight');
  const geography = tablist.getByRole('tab', { name: 'Geography' });
  await expect(geography).toBeFocused();
  await expect(geography).toHaveAttribute('aria-selected', 'true');
  await expect(page).toHaveURL(/[?&]view=geography(&|$)/);
  const panel = page.getByRole('tabpanel');
  await expect(panel).toHaveAttribute('aria-labelledby', (await geography.getAttribute('id')) ?? 'missing');
  await expect(geography).toHaveAttribute('aria-controls', (await panel.getAttribute('id')) ?? 'missing');
  await app.settle();

  await page.keyboard.press('End');
  const salesOps = tablist.getByRole('tab', { name: 'Sales ops' });
  await expect(salesOps).toBeFocused();
  await expect(page).toHaveURL(/[?&]view=sales-ops(&|$)/);
  await app.settle();
  // Sales ops renders a bare `.surface` that used to sit directly in `.main__inner`.
  await expectPanelFlushWithTabs(page);

  await page.keyboard.press('Home');
  await expect(executive).toBeFocused();
  await expect(executive).toHaveAttribute('aria-selected', 'true');
  await expect(page).not.toHaveURL(/view=/);
  await app.settle();
});
