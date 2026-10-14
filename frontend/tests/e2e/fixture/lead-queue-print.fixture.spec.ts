/**
 * W5e w5-lead-table-compile, responsive-08: the virtualized Lead Queue on
 * paper, rendered against the production build at 1440x900.
 *
 * 600 loaded rows are past the virtualization threshold, so the screen
 * keeps a window. `beforeprint` opens it (every loaded row in the DOM, no
 * spacer), print media lays the table out unclipped and inside the page's
 * inline size with identical ink in dark and light, and `afterprint`
 * re-windows and restores the scroller's offset. The whole path makes no
 * request at all: no read, no refetch, no audit row (a Ctrl/Cmd-P of the
 * queue stays unaudited, as ruled).
 */
import type { Page } from '@playwright/test';
import type { LeadSummary } from '../../../src/types';
import type { FixtureTheme } from './app';
import { LEADS, maskedBorrowerId } from './data/borrowers';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const ROWS = 600;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

/** 600 synthetic rows (masked ids in the production shape, no names or contacts). */
const LOADED: LeadSummary[] = Array.from({ length: ROWS }, (_, index) => ({
  ...LEADS[index % LEADS.length],
  borrower_id: maskedBorrowerId(3000 + index),
  approval_status: 'pending',
  outreach_status: 'none',
}));

function serveLoaded(mockApi: MockApi): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>(LOADED, {
    headers: { 'X-Total-Matching': String(ROWS), 'X-Returned-Rows': String(ROWS) },
  }));
}

const rowCount = (page: Page) => page.locator('.lead-table__table tr[data-borrower-row]').count();

/** color / background / border colours of the header and the first rows, keyed by DOM path. */
async function tableInk(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const out: Record<string, string> = {};
    const table = document.querySelector('.lead-table__table');
    if (!table) return out;
    const walk = (element: Element, path: string) => {
      const style = getComputedStyle(element);
      if (style.display === 'none') return;
      out[path] = [style.color, style.backgroundColor, style.borderTopColor, style.borderBottomColor].join(' | ');
      Array.from(element.children).forEach((child, index) => walk(child, `${path}>${child.tagName}${index}`));
    };
    Array.from(table.children).slice(0, 8).forEach((child, index) => walk(child, `table>${child.tagName}${index}`));
    return out;
  });
}

async function beforePrint(page: Page): Promise<void> {
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
}

async function afterPrint(page: Page): Promise<void> {
  await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
}

test.describe('the virtualized Lead Queue prints every loaded row (responsive-08)', () => {
  test('beforeprint renders all 600 rows unclipped; afterprint re-windows at the saved offset; nothing is requested', async ({ app, page, mockApi }) => {
    serveLoaded(mockApi);
    await app.gotoRoute('/lead-queue');
    await expect(page.locator('.lead-table__table tr[data-borrower-row]').first()).toBeVisible();
    await app.settle();
    const windowed = await rowCount(page);
    expect(windowed, 'the screen keeps a window').toBeLessThan(ROWS);

    const wrap = page.locator('.tbl-wrap').filter({ visible: true }).first();
    await wrap.evaluate((element) => {
      element.scrollTop = 2_000;
    });
    await expect.poll(() => wrap.evaluate((element) => element.scrollTop)).toBeGreaterThan(1_500);
    const saved = await wrap.evaluate((element) => element.scrollTop);
    const naturalLoad = markNaturalLoad(mockApi);

    await beforePrint(page);
    await page.emulateMedia({ media: 'print' });
    expect(await rowCount(page), 'every loaded row is on paper').toBe(ROWS);
    await expect(page.locator('.lead-table__virtual-spacer')).toHaveCount(0);
    await expect(page.locator(`tr[data-borrower-row="${LOADED[ROWS - 1].borrower_id}"]`)).toHaveCount(1);
    await expect(wrap).toHaveCSS('max-height', 'none');
    await expect(wrap).toHaveCSS('overflow-y', 'visible');
    // The table fits the page's inline size: nothing is cut at the right edge.
    const fit = await page.locator('.lead-table__table').evaluate((table) => {
      const surface = table.closest('.surface');
      return { table: table.getBoundingClientRect().width, surface: surface?.getBoundingClientRect().width ?? 0 };
    });
    expect(fit.table, 'the table is no wider than its surface on paper').toBeLessThanOrEqual(fit.surface + 1);
    const pinned = await page.locator('.lead-table__table .tbl-cell--approval').first().evaluate((cell) => getComputedStyle(cell).position);
    expect(pinned, 'paper has no scroller behind the pin').toBe('static');

    await afterPrint(page);
    await page.emulateMedia({ media: 'screen' });
    await expect.poll(() => rowCount(page), { message: 'the window comes back' }).toBeLessThan(ROWS);
    await expect.poll(() => wrap.evaluate((element) => element.scrollTop), { message: 'the offset is restored' })
      .toBeGreaterThan(saved - 2);
    expect(await wrap.evaluate((element) => element.scrollTop)).toBeLessThan(saved + 2);

    expect(mockApi.calls.slice(naturalLoad).map((call) => `${call.method} ${call.path}`), 'a print requests nothing').toEqual([]);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'lead-queue print');
  });

  test('the loaded rows print the same ink in dark and light', async ({ app, page, mockApi }) => {
    serveLoaded(mockApi);
    const sweeps: Array<Record<string, string>> = [];
    for (const theme of THEMES) {
      await page.emulateMedia({ media: 'screen', colorScheme: theme });
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await expect(page.locator('.lead-table__table tr[data-borrower-row]').first()).toBeVisible();
      await app.settle();
      await beforePrint(page);
      await page.emulateMedia({ media: 'print', colorScheme: theme });
      await expect.poll(() => page.evaluate(() => document.getAnimations().filter((a) => a.playState === 'running').length))
        .toBe(0);
      expect(await rowCount(page)).toBe(ROWS);
      sweeps.push(await tableInk(page));
      await afterPrint(page);
    }
    const [dark, light] = sweeps;
    expect(Object.keys(dark).length, 'the sweep saw the table').toBeGreaterThan(40);
    expect(Object.keys(light).sort()).toEqual(Object.keys(dark).sort());
    const differing = Object.keys(dark).filter((key) => dark[key] !== light[key]).map((key) => `${key}: ${dark[key]} vs ${light[key]}`);
    expect(differing, 'paper ink differs between dark and light').toEqual([]);
  });
});
