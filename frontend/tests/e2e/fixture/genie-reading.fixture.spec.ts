/**
 * Rendered-layer proofs for the wave-3 Genie reading lane (audit 2026-09-21
 * stack-02, genie-07, genie-08, motion-v2, genie-06 slice 2), 1440x900,
 * production build:
 *   - the answer grammar: ordered list, heading, italic, inert link and a
 *     narrative pipe table shown verbatim (never a <table>);
 *   - reading an earlier turn while an answer lands does not move the
 *     reader, and "New answer" takes them to it;
 *   - earlier turns restored at mount collapse on both surfaces;
 *   - "Show all" 120 x 7 rows: windowed, the last row reachable, no
 *     sideways overflow, the panel box unchanged;
 *   - the audited CSV: the receipt POST precedes the download, the
 *     declaration holds no question text, the file carries provenance, and a
 *     404 or 503 downloads nothing; the trimmed History replay copy;
 *   - axe on the panel and the route with a five-section answer, both themes;
 *   - no audited read beyond the turn and the export receipt.
 * Turns are scripted with data/genieTurn.ts; stored turns are seeded into
 * sessionStorage before the app boots.
 */
import { readFile } from 'node:fs/promises';
import type { Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import {
  earlierTurns,
  fiveSectionAnswer,
  INERT_LINK_HOST,
  READING_QUESTION,
  readingAnswer,
  registerExportReceipt,
  trimmedReplayAnswer,
  type StoredTurn,
} from './data/genieReading';
import { registerGenieTurn } from './data/genieTurn';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const TURNS_KEY = 'mip-genie-conversation-v1';
/** Reads that write VIEW_* / DRAFT_* audit rows: none may fire from reading an answer. */
const AUDITED_READ = /^\/api\/(leads(\/|$)|borrowers\/|outreach\/draft|offers\/recommend)|\/proof$/;

type ExportStep = 'receipt-request' | 'receipt-response' | 'download';

declare global {
  interface Window {
    __mipGenieExportOrder?: ExportStep[];
  }
}

/** Seed the transcript store once per tab, before the app boots. */
async function seedTurns(page: Page, turns: StoredTurn[]): Promise<void> {
  await page.addInitScript(
    ([key, value]) => {
      if (!window.sessionStorage.getItem(key)) window.sessionStorage.setItem(key, value);
    },
    [TURNS_KEY, JSON.stringify(turns)] as const,
  );
}

/** Record, in page order, the receipt request, its response and every anchor download. */
function installExportOrderProbe(): void {
  const order: ExportStep[] = [];
  window.__mipGenieExportOrder = order;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const isReceipt = url.includes('/genie/export-receipt');
    if (isReceipt) order.push('receipt-request');
    const response = await originalFetch(input, init);
    if (isReceipt) order.push('receipt-response');
    return response;
  };
  const originalClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function click(this: HTMLAnchorElement) {
    if (this.download) order.push('download');
    return originalClick.call(this);
  };
}

const exportOrder = (page: Page) => page.evaluate(() => [...(window.__mipGenieExportOrder ?? [])]);

function thread(page: Page) {
  return page.locator('#main-content .genie-thread');
}

test.describe('the answer grammar', () => {
  test('renders numbered lists, headings, italics, inert links and a narrative table', async ({ app, mockApi }) => {
    registerGenieTurn(mockApi, { answer: readingAnswer(), holdProgress: false });
    await app.gotoRoute('/ask-genie');
    const dialog = await app.askGenie(READING_QUESTION);
    const answer = dialog.locator('.genie__msg--ai .genie-answer');
    await expect(answer).toHaveCount(1, { timeout: 20_000 });

    await expect(answer.locator('h3.genie-md-p--heading')).toHaveText('Where refinance demand sits');
    await expect(answer.locator('ol.genie-md-list--ordered > li')).toHaveCount(3);
    await expect(answer.locator('em')).toHaveText('prime');
    await expect(answer).toContainText('See the rate table for the spread.');
    await expect(answer.locator(`a[href*="${INERT_LINK_HOST}"]`)).toHaveCount(0);
    const narrative = answer.locator('figure.genie-md-pre');
    await expect(narrative.locator('figcaption')).toHaveText("As written in Genie's narrative");
    await expect(narrative.locator('pre')).toContainText('| State | Candidates |');
    await expect(narrative.locator('table')).toHaveCount(0);
    await expect(answer).not.toContainText('###');
  });
});

test.describe('reading in the panel', () => {
  test('an answer landing while the reader reads an earlier turn does not move them; New answer does', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi, { answer: readingAnswer(), holdComplete: true });
    await seedTurns(
      page,
      earlierTurns(2).map((stored, i) => ({ ...stored, response: readingAnswer({ message_id: `fixture-message-long-${i}` }) })),
    );
    await app.gotoRoute('/ask-genie');
    const dialog = await app.askGenie(READING_QUESTION);
    await expect(dialog.locator('.genie-progress')).toBeVisible();
    const body = dialog.locator('.genie__body');

    // The reader scrolls up to the first earlier turn with the wheel.
    await body.hover();
    await page.mouse.wheel(0, -20_000);
    await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeLessThan(2);
    const before = await body.evaluate((el) => el.scrollTop);

    turn.finishGenieTurn();
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);
    turn.releaseComplete();
    await expect(dialog.locator('.genie__msg--ai .genie-answer')).toHaveCount(3);

    const after = await body.evaluate((el) => el.scrollTop);
    expect(Math.abs(after - before)).toBeLessThanOrEqual(1);
    const jump = dialog.getByRole('button', { name: 'New answer' });
    await expect(jump).toBeVisible();

    await jump.click();
    await expect(jump).toHaveCount(0);
    await expect
      .poll(() => page.evaluate(() => {
        const active = document.activeElement;
        const answers = document.querySelectorAll('.genie .genie__msg--ai');
        return active === answers[answers.length - 1];
      }))
      .toBe(true);
  });
});

test.describe('earlier turns collapse', () => {
  test('a restored thread collapses all but its latest answer on the route and in the panel', async ({ app, page }) => {
    await seedTurns(page, earlierTurns(3));
    await app.gotoRoute('/ask-genie');
    const route = thread(page);
    await expect(route.locator('.genie-collapse')).toHaveCount(2);
    await expect(route.locator('.genie-answer')).toHaveCount(1);
    await expect(route.locator('.genie__msg--user')).toHaveCount(3);
    await route.getByRole('button', { name: 'Show full answer' }).first().click();
    await expect(route.locator('.genie-answer')).toHaveCount(2);
    await expect(route.getByRole('button', { name: 'Collapse answer' })).toHaveAttribute('aria-expanded', 'true');

    const dialog = await app.openGenie();
    await expect(dialog.locator('.genie-collapse')).toHaveCount(2);
    await expect(dialog.locator('.genie__msg--ai .genie-answer')).toHaveCount(1);
    await dialog.getByRole('button', { name: 'Show full answer' }).first().click();
    await expect(dialog.locator('.genie__msg--ai .genie-answer')).toHaveCount(2);
  });
});

test.describe('every row and column', () => {
  test('Show all windows 120 x 7 rows in place, reaches the last row, and never widens the page or the panel', async ({ app, page, mockApi }) => {
    await seedTurns(page, [{ question: READING_QUESTION, response: readingAnswer() }]);
    await app.gotoRoute('/ask-genie');
    const checkpoint = mockApi.calls.length;
    const route = thread(page);
    await route.getByRole('button', { name: 'Show all 120 rows and 7 columns' }).click();
    const region = route.getByRole('region', { name: 'All 120 rows of this answer' });
    await expect(region).toBeVisible();
    await expect(region.locator('thead th')).toHaveCount(7);
    expect(await region.locator('tbody tr[aria-rowindex]').count()).toBeLessThan(120);
    await expect(region.locator('table')).toHaveAttribute('aria-rowcount', '121');
    await region.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await expect(region.locator('tbody tr[aria-rowindex="121"]')).toBeVisible();
    const sideways = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('#main-content .surface')].some((el) => el.scrollWidth > el.clientWidth + 1),
    );
    expect(sideways).toBe(false);

    const dialog = await app.openGenie();
    const box = await dialog.boundingBox();
    await dialog.getByRole('button', { name: 'Show all 120 rows and 7 columns' }).click();
    await expect(dialog.getByRole('region', { name: 'All 120 rows of this answer' })).toBeVisible();
    expect(await dialog.boundingBox()).toEqual(box);
    expect(mockApi.calls.slice(checkpoint).filter((call) => AUDITED_READ.test(call.path))).toEqual([]);
  });

  test('a History replay that kept 50 of 120 rows says so', async ({ app, page }) => {
    await seedTurns(page, [{ question: READING_QUESTION, response: trimmedReplayAnswer('fixture-message-trimmed') }]);
    await app.gotoRoute('/ask-genie');
    const route = thread(page);
    await route.getByRole('button', { name: 'Show all 50 rows held (of 120)' }).click();
    await expect(route.locator('.genie-answer__rows-note')).toHaveText(
      'Restored from History: 50 of 120 rows were kept. Ask again for the complete result.',
    );
  });
});

test.describe('audited CSV download', () => {
  test('the receipt POST precedes the download; the declaration carries no question text', async ({ app, page, mockApi }) => {
    await page.addInitScript(installExportOrderProbe);
    const receipt = registerExportReceipt(mockApi, 200);
    await seedTurns(page, [{ question: READING_QUESTION, response: readingAnswer() }]);
    await app.gotoRoute('/ask-genie');
    const checkpoint = mockApi.calls.length;

    const downloadEvent = page.waitForEvent('download');
    await thread(page).getByRole('button', { name: 'Download CSV' }).click();
    const download = await downloadEvent;

    expect(await exportOrder(page)).toEqual(['receipt-request', 'receipt-response', 'download']);
    expect(receipt.bodies).toHaveLength(1);
    const declaration = receipt.bodies[0] as Record<string, unknown>;
    expect(Object.keys(declaration).sort()).toEqual([
      'answer_row_count',
      'columns_sha256',
      'conversation_id',
      'csv_sha256',
      'message_id',
      'row_count',
      'scope',
    ]);
    expect(JSON.stringify(declaration)).not.toContain(READING_QUESTION);
    expect(declaration).toMatchObject({ scope: 'answer', row_count: 120, answer_row_count: 120 });

    const csv = await readFile(await download.path(), 'utf8');
    const lines = csv.split('\n');
    expect(lines.slice(0, 8).map((line) => line.split('=')[0])).toEqual([
      '# generated_at',
      '# source',
      '# trusted_assets',
      '# export_scope',
      '# section_index',
      '# exported_rows',
      '# answer_row_count',
      '# rows_complete',
    ]);
    expect(lines[8]).toBe('state,segment_code,borrowers,avg_score,avg_rate_spread_bps,avg_equity_pct,contactable');
    expect(lines).toHaveLength(8 + 1 + 120);
    await expect(thread(page).locator('[data-export-status]')).toHaveText(
      'CSV downloaded. The export is recorded in the audit log.',
    );
    const genieCalls = mockApi.calls.slice(checkpoint).map((call) => call.path);
    expect(genieCalls.filter((path) => AUDITED_READ.test(path))).toEqual([]);
  });

  for (const [status, message] of [
    [404, "This answer can't be exported: it is not in your Genie history."],
    [503, 'Export not recorded, so nothing was downloaded.'],
  ] as const) {
    test(`a ${status} receipt downloads nothing and says why`, async ({ app, page, mockApi }) => {
      await page.addInitScript(installExportOrderProbe);
      registerExportReceipt(mockApi, 200);
      app.degrade('/api/genie/export-receipt', { method: 'POST', status, body: { detail: 'refused' } });
      let downloads = 0;
      page.on('download', () => {
        downloads += 1;
      });
      await seedTurns(page, [{ question: READING_QUESTION, response: readingAnswer() }]);
      await app.gotoRoute('/ask-genie');

      await thread(page).getByRole('button', { name: 'Download CSV' }).click();
      await expect(thread(page).locator('[data-export-status]')).toHaveText(message);
      expect(await exportOrder(page)).toEqual(['receipt-request', 'receipt-response']);
      expect(downloads).toBe(0);
      await expect(thread(page).getByRole('button', { name: 'Download CSV' })).toBeEnabled();
    });
  }
});

test.describe('accessibility of a long answer', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`a five-section answer is axe-clean on the route and in the panel (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await seedTurns(page, [{ question: READING_QUESTION, response: fiveSectionAnswer() }]);
      await app.gotoRoute('/ask-genie');
      const route = thread(page);
      await expect(route.locator('.genie-answer__section-toggle[aria-expanded="true"]')).toHaveCount(5);
      await expectAxeClean(page, {
        key: { route: 'ask-genie', state: 'genie-reading-five-sections' },
        theme,
        known: {},
        include: '#main-content .genie-thread',
      });

      const dialog = await app.openGenie();
      await expect(dialog.locator('.genie-answer__section-toggle[aria-expanded="true"]')).toHaveCount(5);
      await expectAxeClean(page, {
        key: { route: 'genie-panel', state: 'genie-reading-five-sections' },
        theme,
        known: {},
        include: '.genie[role="dialog"]',
      });
    });
  }
});
