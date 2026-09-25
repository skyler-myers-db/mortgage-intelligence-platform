/**
 * Genie History on the query layer (audit 2026-09-21 `runtime-06`, Genie
 * slice), proven on the rendered floating panel at 1440x900 in both themes:
 *
 *  - opening the panel reads no history; opening History makes exactly one
 *    GET /api/genie/sessions, and each reopen exactly one more;
 *  - a reopen paints the cached rows at once, while its own read is still in
 *    flight (the read is held open by the fixture to prove it);
 *  - a session whose last_activity_at is null (the wire's `str | None`,
 *    audit quality-04) renders "1 turn" with no timestamp, no "null" and no
 *    "Invalid Date";
 *  - a 500 shows "History unavailable" after exactly ONE request (no retry);
 *  - nothing here opens an audited read, and the open menu is axe-clean with
 *    no ratchet entry.
 */
import type { Locator } from '@playwright/test';
import type { GenieSessionSummary } from '../../../src/types';
import { expectAxeClean, type AxeTheme } from './axe';
import { GENIE_HISTORY_SESSIONS } from './data/genie';
import { json, normalizeApiPath, type MockApi } from './mockApi';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const THEMES: readonly AxeTheme[] = ['light', 'dark'];
const SESSIONS_PATH = '/api/genie/sessions';

/** Completed (fulfilled) GET /api/genie/sessions calls. */
function historyReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && normalizeApiPath(call.path) === SESSIONS_PATH).length;
}

function historyToggle(genie: Locator): Locator {
  return genie.getByRole('button', { name: 'Genie conversation history' });
}

for (const theme of THEMES) {
  test(`${theme}: History reads once per open and repaints cached rows while a reopen reads`, async ({ app, mockApi, page }) => {
    let served = 0;
    let held: Promise<void> | null = null;
    let release: () => void = () => undefined;
    mockApi.register('GET', SESSIONS_PATH, async () => {
      served += 1;
      if (held) await held;
      return json<{ sessions: GenieSessionSummary[] }>({ sessions: GENIE_HISTORY_SESSIONS });
    });
    await app.setTheme(theme);
    await app.gotoRoute('/');
    const naturalLoad = markNaturalLoad(mockApi);

    const genie = await app.openGenie();
    expect(historyReads(mockApi), 'opening the panel reads no history').toBe(0);

    await historyToggle(genie).click();
    const menu = genie.getByRole('menu', { name: 'Past Genie conversations' });
    const rows = menu.getByRole('menuitem');
    await expect(rows).toHaveCount(2);
    await expect.poll(() => historyReads(mockApi)).toBe(1);

    const quiet = rows.filter({ hasText: 'Quiet thread' });
    await expect(quiet.locator('.genie-history__meta')).toHaveText('1 turn');
    await expect(menu).not.toContainText('null');
    await expect(menu).not.toContainText('Invalid Date');
    await expect(rows.filter({ hasText: 'Equity sweep by state' }).locator('.genie-history__meta')).toContainText('3 turns · ');

    await expectAxeClean(page, { key: { route: 'home', state: 'genie-history' }, theme, known: {}, include: '.genie[role="dialog"]' });

    // Close, then hold the reopen's read open: the cached rows paint at once.
    await historyToggle(genie).click();
    await expect(menu).toHaveCount(0);
    held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await historyToggle(genie).click();
    await expect.poll(() => served, 'the reopen reads again').toBe(2);
    await expect(rows).toHaveCount(2);
    await expect(menu).not.toContainText('Loading history');
    expect(historyReads(mockApi), 'the reopen read is still in flight').toBe(1);

    release();
    await expect.poll(() => historyReads(mockApi)).toBe(2);
    await expect(rows).toHaveCount(2);
    await app.settle();
    expect(historyReads(mockApi), 'one read per open, never a background refetch').toBe(2);
    expectNoAuditedReadSince(mockApi, naturalLoad, `genie history · ${theme}`);
  });

  test(`${theme}: a failing history read shows "History unavailable" after exactly one request`, async ({ app, mockApi }) => {
    app.degrade(SESSIONS_PATH, { status: 500, body: { detail: 'fixture: history is down' } });
    await app.setTheme(theme);
    await app.gotoRoute('/');
    const naturalLoad = markNaturalLoad(mockApi);

    const genie = await app.openGenie();
    await historyToggle(genie).click();
    const menu = genie.getByRole('menu', { name: 'Past Genie conversations' });
    await expect(menu.locator('.genie-history__state--error')).toHaveText('History unavailable');
    await expect(menu.getByRole('menuitem')).toHaveCount(0);

    // Quiet window: a retry would land here.
    await app.settle();
    expect(historyReads(mockApi), 'no retry after the failed read').toBe(1);
    expectNoAuditedReadSince(mockApi, naturalLoad, `genie history error · ${theme}`);
  });
}
