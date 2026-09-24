/**
 * Real-pixel proofs that forced colors (Windows High Contrast) keeps every
 * Highlight state readable (2026-09-21 audit responsive-v3 / a11y-10), in
 * both forced palettes. Source contract: contrastModes.css.test.ts.
 *
 * Why pixels: Chromium paints a Canvas readability backplate behind every
 * text run, whatever the element's own background is. A label inked
 * HighlightText therefore computes to a perfect HighlightText-on-Highlight
 * pair and paints as a solid Canvas box with no visible words; a
 * getComputedStyle check passes on exactly that defect. paintedInk.ts
 * samples each text run's own Range rect and each svg glyph's box from a real
 * screenshot, so the verdict is what the user sees.
 */
import type { Locator, Page } from '@playwright/test';
import { SAMPLE_SCALE, describeInk, insetRingBand, paintedInks, paintedSystemFill, sameColor, type PaintedInk } from './paintedInk';
import { contrastRatio, settleTransitions, tokenValue, type Rgb } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const OWNER_LINK = 'Portfolio investor (5+)';

// Layout stays 1440x900 CSS px; only the raster is finer (see paintedInk.ts).
test.use({ deviceScaleFactor: SAMPLE_SCALE });

/**
 * Every sample clears its floor: 4.5:1 for a text run on its backplate
 * (WCAG 1.4.3), 3:1 for a glyph on the Highlight fill (1.4.11). The state
 * must yield the sample kinds it is known to hold, so it cannot pass empty.
 * A glyph's ground must BE the Highlight fill, which proves the state paints.
 */
async function expectReadable(
  page: Page,
  state: string,
  target: Locator,
  expects: { text: boolean; glyph: boolean; fill: Rgb },
): Promise<PaintedInk[]> {
  const inks = await paintedInks(page, target, expects.fill);
  const listing = inks.map((ink) => describeInk(state, ink)).join('\n');
  if (expects.text) expect(inks.some((ink) => ink.kind === 'text'), `${state}: a text run is sampled\n${listing}`).toBe(true);
  if (expects.glyph) expect(inks.some((ink) => ink.kind === 'glyph'), `${state}: a glyph is sampled\n${listing}`).toBe(true);
  // Soft, so one run reports every state that fails, not only the first.
  for (const ink of inks) {
    expect.soft(ink.ratio, describeInk(state, ink)).toBeGreaterThanOrEqual(ink.kind === 'text' ? 4.5 : 3);
    if (ink.kind === 'glyph') {
      expect.soft(sameColor(ink.ground, expects.fill), `${describeInk(state, ink)}: glyph sits on the Highlight fill`).toBe(true);
    }
  }
  return inks;
}

/** A detached class probe (no drawer or borrower read is opened): a state label with a glyph. */
async function probe(page: Page, className: string, label: string): Promise<Locator> {
  await page.locator('#main-content').evaluate(
    (main, { cls, text }) => {
      const host = document.createElement('div');
      host.dataset.forcedInkProbe = cls;
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = cls;
      tab.innerHTML =
        '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">' +
        '<path d="M4 6h16M4 12h16M4 18h10"/></svg>';
      tab.append(` ${text}`);
      host.appendChild(tab);
      main.prepend(host);
    },
    { cls: className, text: label },
  );
  return page.locator(`[data-forced-ink-probe="${className}"] > button`);
}

test.describe('forced colors paints every Highlight state legibly (responsive-v3 / a11y-10)', () => {
  for (const theme of THEMES) {
    test.describe(theme, () => {
      test.beforeEach(async ({ app, page }) => {
        await app.setTheme(theme);
        await page.emulateMedia({ forcedColors: 'active' });
      });

      test('every text run and glyph inside a Highlight state is readable in the real pixels', async ({ app, page }) => {
        // State applied by URL: the State pill goes active, and an Owner Link
        // hero chip (with its remove glyph) and the More filters toggle join it.
        await app.gotoRoute(`/lead-queue?state=IL&owner_link=${encodeURIComponent(OWNER_LINK)}`);
        expect(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), 'precondition: forced colors').toBe(true);
        const fill = await paintedSystemFill(page, 'Highlight');
        const both = { text: true, glyph: true, fill };

        const nav = page.getByRole('navigation', { name: 'Main navigation' });
        const current = await expectReadable(page, 'current-page route-nav link', nav.locator('.filter.is-active'), both);
        expect(current.map((ink) => ink.what)).toContain('filter__value "Leads"');
        await expectReadable(page, 'current rail item', page.locator('.rail__item.is-active'), both);

        const statePill = page.getByRole('combobox', { name: 'STATE: IL' });
        const applied: Record<string, Locator> = {
          'STATE pill': statePill,
          'More filters toggle': page.getByTestId('lead-queue-more-filters'),
          'OWNER LINK hero chip': page.getByTestId('lead-queue-active-filters').locator('.filter.is-active'),
        };
        for (const [name, chip] of Object.entries(applied)) {
          await expect(chip, name).toHaveClass(/\bis-active\b/);
          await expectReadable(page, name, chip, both);
        }

        // The filter menu: its cursor row and the selected row both take the fill.
        await statePill.focus();
        await page.keyboard.press('ArrowDown');
        const menu = page.getByRole('listbox', { name: 'STATE' });
        await expect(menu).toBeVisible();
        await page.keyboard.press('ArrowDown');
        const cursor = menu.locator('.filter-menu__item.is-focused');
        const selected = menu.locator('.filter-menu__item.is-selected:not(.is-focused)');
        await expect(cursor).toHaveCount(1);
        await expect(selected).toHaveCount(1);
        await expectReadable(page, 'filter-menu cursor row', cursor, { ...both, glyph: false });
        await expectReadable(page, 'filter-menu selected row', selected, { ...both, glyph: false });
        // Both rows share the fill, so the cursor's inset ring is what tells
        // them apart: it must stand off the fill (WCAG 1.4.11), where a
        // Highlight ring on the Highlight fill was 1:1.
        const ringWidth = Number.parseFloat(await tokenValue(cursor, '--focus-ring-width'));
        const ring = await insetRingBand(page, cursor, ringWidth);
        const plain = await insetRingBand(page, selected, ringWidth);
        const ringRatio = contrastRatio(ring.ring, ring.inside);
        const described = `cursor ring rgb(${ring.ring.join(', ')}) on fill rgb(${ring.inside.join(', ')}) at ${ringRatio.toFixed(2)}:1`;
        expect.soft(sameColor(ring.inside, fill), `${described}: the cursor row keeps the Highlight fill`).toBe(true);
        expect.soft(ringRatio, described).toBeGreaterThanOrEqual(3);
        expect.soft(sameColor(plain.ring, plain.inside), 'the selected row wears no ring').toBe(true);
        await page.keyboard.press('Escape');
        await expect(menu).toHaveCount(0);

        // Drawer and proof tabs, as class probes (no drawer or borrower read opens).
        for (const cls of ['drawer__tab is-active', 'proof-tab is-active']) {
          await expectReadable(page, cls, await probe(page, cls, 'Evidence'), both);
        }

        const consolePanel = await app.openConsole();
        const pressed = consolePanel.getByRole('group', { name: 'Density' }).locator('button[aria-pressed="true"]');
        await expectReadable(page, 'Console density pressed button', pressed, { ...both, glyph: false });
        const consoleToggle = page.getByRole('banner').getByRole('button', { name: 'Toggle console' });
        await expect(consoleToggle).toHaveClass(/\bis-active\b/);
        await expectReadable(page, 'Console toggle', consoleToggle, { ...both, text: false });

        const palette = await app.openCommandPalette();
        await page.keyboard.press('ArrowDown');
        const row = palette.locator('.cmdk__row.is-active');
        await expect(row).toHaveCount(1);
        const rowInks = await expectReadable(page, 'active palette row', row, both);
        const owners = rowInks.map((ink) => ink.what.split(' ')[0]);
        expect(owners).toEqual(expect.arrayContaining(['cmdk__row-label', 'cmdk__row-hint']));
      });

      test("an applied chip's remove glyph stays visible at rest, on hover and on keyboard focus", async ({ app, page }) => {
        await app.gotoRoute(`/lead-queue?owner_link=${encodeURIComponent(OWNER_LINK)}`);
        const fill = await paintedSystemFill(page, 'Highlight');
        const chip = page.getByTestId('lead-queue-active-filters').locator('.filter.is-active');
        const remove = chip.getByRole('button', { name: `Remove Owner Link: ${OWNER_LINK} filter` });
        await expect(remove).toBeVisible();
        const glyphOf = async (state: string, ground?: Rgb) => {
          await settleTransitions(remove);
          const inks = (await paintedInks(page, remove)).filter((ink) => ink.kind === 'glyph');
          expect(inks, `${state}: the cross glyph is sampled`).toHaveLength(1);
          expect.soft(inks[0].ratio, describeInk(state, inks[0])).toBeGreaterThanOrEqual(3);
          if (ground) expect.soft(sameColor(inks[0].ground, ground), `${describeInk(state, inks[0])}: on the chip's Highlight fill`).toBe(true);
        };
        await glyphOf('remove at rest', fill);

        await remove.hover();
        expect(await remove.evaluate((el) => el.matches(':hover'))).toBe(true);
        await glyphOf('remove on hover');

        // Keyboard: Tab from the page's first stop until the remove button
        // holds focus, with the pointer parked where it hovers nothing.
        await page.mouse.move(1, 899);
        await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
        let reached = false;
        for (let presses = 0; presses < 80 && !reached; presses += 1) {
          await page.keyboard.press('Tab');
          reached = await remove.evaluate((el) => el === document.activeElement);
        }
        expect(reached, 'the remove button is reachable by Tab').toBe(true);
        expect(await remove.evaluate((el) => ({ focus: el.matches(':focus-visible'), hover: el.matches(':hover') }))).toEqual({
          focus: true,
          hover: false,
        });
        await glyphOf('remove on keyboard focus');
      });
    });
  }
});
