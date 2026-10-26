/**
 * W5e w5-lead-table-compile, responsive-08: the virtualized Lead Queue on
 * paper, rendered against the production build at 1440x900.
 *
 * 600 loaded rows are past the virtualization threshold, so the screen
 * keeps a window. `beforeprint` opens it (every loaded row in the DOM, no
 * spacer), print media lays the table out unclipped and inside the page's
 * inline size with identical ink in dark and light, and `afterprint`
 * re-windows and restores the scroller's offset (the scroller keeps its
 * screen fill height through the print, so the restore has a scroll range
 * however soon afterprint follows the screen layout). The whole path makes no
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

/**
 * The ink sweep needs the virtualized print path, not 600 rows: just past
 * LEAD_VIRTUALIZATION_THRESHOLD (120) keeps the print-media restyle cheap.
 */
const INK_ROWS = 130;

/** Synthetic rows (masked ids in the production shape, no names or contacts). */
function loadedRows(count: number): LeadSummary[] {
  return Array.from({ length: count }, (_, index) => ({
    ...LEADS[index % LEADS.length],
    borrower_id: maskedBorrowerId(3000 + index),
    approval_status: 'pending',
    outreach_status: 'none',
  }));
}
const LOADED = loadedRows(ROWS);

function serveLoaded(mockApi: MockApi, rows: LeadSummary[] = LOADED): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>(rows, {
    headers: { 'X-Total-Matching': String(rows.length), 'X-Returned-Rows': String(rows.length) },
  }));
}

const rowCount = (page: Page) => page.locator('.lead-table__table tr[data-borrower-row]').count();

/**
 * The shell's own background polls (connection probe, Follow-ups badge,
 * queue freshness): wall-clock timers, not the print. Everything else the
 * page requests after the natural load would be the print's doing.
 */
const SHELL_POLLS = new Set(['GET /api/health', 'GET /api/sales/followups/summary', 'GET /api/workspace/queue-version']);

/**
 * color / background / border colours of the header and the first eight rows,
 * keyed by DOM path. An element with a running animation (a status
 * indicator's pulse) reads as 'animating': its ink is a moment, not the page's.
 */
async function tableInk(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const out: Record<string, string> = {};
    const table = document.querySelector('.lead-table__table');
    if (!table) return out;
    const walk = (element: Element, path: string) => {
      const style = getComputedStyle(element);
      if (style.display === 'none') return;
      out[path] = element.getAnimations().some((animation) => animation.playState === 'running')
        ? 'animating'
        : [style.color, style.backgroundColor, style.borderTopColor, style.borderBottomColor].join(' | ');
      Array.from(element.children).forEach((child, index) => walk(child, `${path}>${child.tagName}${index}`));
    };
    const head = table.querySelector('thead');
    if (head) walk(head, 'thead');
    Array.from(table.querySelectorAll('tbody > tr')).slice(0, 8).forEach((row, index) => walk(row, `tbody>tr${index}`));
    return out;
  });
}

async function beforePrint(page: Page): Promise<void> {
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
}

async function afterPrint(page: Page): Promise<void> {
  await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
}

/**
 * Resolves after three animation frames: a ResizeObserver delivery and the
 * frame the fill-height measurement waits for (useLeadTableFillHeight) have
 * both run.
 */
async function threeFrames(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    let left = 3;
    const tick = () => {
      left -= 1;
      if (left === 0) resolve();
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }));
}

test.describe('the virtualized Lead Queue prints every loaded row (responsive-08)', () => {
  // 600 rows mount at once on beforeprint: a deliberately heavy render.
  test.slow();
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
    // The scroller's height to the fold of <main>, measured for the screen.
    const fillHeight = () => wrap.evaluate((element) => element.style.getPropertyValue('--lead-table-fill-block'));
    const screenFill = await fillHeight();
    expect(screenFill, 'the screen measured the scroller').toMatch(/^\d+px$/);
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
    // Frames ran in print media. Had the paper height of <main> (26,879px)
    // become the fill height, the scroller would come back from the print
    // unclipped, and an afterprint that beat the next screen frame restored
    // into an empty scroll range: the top (W5e CI run 40311237233).
    await threeFrames(page);
    expect(await fillHeight(), 'the paper layout leaves the screen fill height alone').toBe(screenFill);

    // The browser lays the page out for the screen again, then fires afterprint.
    await page.emulateMedia({ media: 'screen' });
    await afterPrint(page);
    await expect.poll(() => rowCount(page), { message: 'the window comes back' }).toBeLessThan(ROWS);
    await expect.poll(() => wrap.evaluate((element) => element.scrollTop), { message: 'the offset is restored' })
      .toBeGreaterThan(saved - 2);
    expect(await wrap.evaluate((element) => element.scrollTop)).toBeLessThan(saved + 2);

    const requested = mockApi.calls.slice(naturalLoad).map((call) => `${call.method} ${call.path}`);
    expect(requested.filter((call) => !SHELL_POLLS.has(call)), 'a print requests nothing').toEqual([]);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'lead-queue print');
  });

  test('the loaded rows print the same ink in dark and light', async ({ app, page, mockApi }) => {
    serveLoaded(mockApi, loadedRows(INK_ROWS));
    const sweeps: Array<Record<string, string>> = [];
    for (const theme of THEMES) {
      await page.emulateMedia({ media: 'screen', colorScheme: theme });
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await expect(page.locator('.lead-table__table tr[data-borrower-row]').first()).toBeVisible();
      await app.settle();
      await beforePrint(page);
      await page.emulateMedia({ media: 'print', colorScheme: theme, reducedMotion: 'reduce' });
      expect(await rowCount(page)).toBe(INK_ROWS);
      // The ink once it holds still (a colour transition settles): two equal
      // sweeps in a row. A print-media restyle is slow on a loaded host.
      let previous = '';
      await expect.poll(async () => {
        const now = JSON.stringify(await tableInk(page));
        const settled = now === previous;
        previous = now;
        return settled;
      }, { intervals: [250], timeout: 60_000 }).toBe(true);
      sweeps.push(JSON.parse(previous) as Record<string, string>);
      await page.emulateMedia({ media: 'screen', colorScheme: theme });
      await afterPrint(page);
    }
    const [dark, light] = sweeps;
    expect(Object.keys(dark).length, 'the sweep saw the table').toBeGreaterThan(40);
    expect(Object.keys(light).sort()).toEqual(Object.keys(dark).sort());
    const differing = Object.keys(dark).filter((key) => dark[key] !== light[key]).map((key) => `${key}: ${dark[key]} vs ${light[key]}`);
    expect(differing, 'paper ink differs between dark and light').toEqual([]);
  });
});
