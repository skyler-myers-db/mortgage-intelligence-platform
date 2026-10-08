/**
 * Follow-ups due and Snooze on the rendered production build (W5d
 * w5-followups-snooze; D-shell-deviations-a, audit shell-09, wow-power-3;
 * deviation:followups-rail-item, deviation:followups-tray), at 1440x900 in
 * both themes unless stated:
 *
 *  (a) the eligible actor's rail shows the Due item with its dot; the tray's
 *      list is read exactly once, on open, never before; neither the rail nor
 *      the tray opens an audited read (/leads, /borrowers/*, /outreach/draft,
 *      /offers/recommend);
 *  (b) axe-clean with the tray open: the whole page with the Console closed,
 *      and the rail plus the tray with the Console open;
 *  (c) .main never overflows sideways; the tray fits a 1280x720 laptop; at
 *      1152x720 the Console is a bottom sheet and the tray's centre is still
 *      the tray (the top layer wins);
 *  (d) a slow snooze keeps the row with the chosen option loading until it
 *      answers, sends exactly {snooze_days, request_id}, then the row leaves
 *      and the success toast shows; focus then moves to the next VISIBLE row,
 *      else the tray heading (never <body>); a 409 not_contactable keeps the
 *      row and re-reads the list;
 *  (e) the default (ineligible) actor gets no item and no list read.
 */
import type { Page } from '@playwright/test';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { FOLLOWUP_IDS, registerFollowUps, SNOOZE_NOT_CONTACTABLE_409 } from './data/followUps';
import { json, normalizeApiPath, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const TRIGGER = '.rail button.rail__item:has(.mod:text-is("Due"))';

function listReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && normalizeApiPath(call.path) === '/api/sales/followups').length;
}

/**
 * Open the tray. `keyboard`: below 1280px the open Console is a bottom sheet
 * over the rail's lower items, so the trigger is reached by focus and Enter.
 */
async function openTray(page: Page, { keyboard = false }: { keyboard?: boolean } = {}) {
  const trigger = page.locator(TRIGGER);
  if (keyboard) {
    await trigger.focus();
    await page.keyboard.press('Enter');
  } else {
    await trigger.click();
  }
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  const tray = page.getByRole('region', { name: 'Follow-ups' });
  await expect(tray.locator('.followups__row').first()).toBeVisible();
  return { trigger, tray };
}

async function overflowsSideways(page: Page): Promise<boolean> {
  return page.locator('.main').evaluate((main) => main.scrollWidth > main.clientWidth + 1);
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`follow-ups due (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('(a) the dot shows, the list is read once on open, and nothing audited is read', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      await app.gotoRoute('/');
      const naturalLoad = markNaturalLoad(mockApi);
      const trigger = page.locator(TRIGGER);
      await expect(trigger).toBeVisible();
      await expect(trigger).toHaveClass(/has-due/);
      await expect(trigger).toHaveAccessibleName('Due follow-ups');
      await expect(trigger).toHaveAccessibleDescription('Something is due today or overdue');
      await app.settle();
      expect(listReads(mockApi), 'no list read before the tray opens').toBe(0);

      const { tray } = await openTray(page);
      await app.settle();
      expect(listReads(mockApi)).toBe(1);
      await expect(tray.locator('.followups__section-title')).toHaveText([
        'Overdue', 'Today', 'This week', 'Follow-ups you requested', 'Newly assigned', 'Older (1)', 'No longer contactable (3)',
      ]);
      expectNoAuditedReadSince(mockApi, naturalLoad, 'follow-ups rail and tray');
    });

    test('(b) axe-clean with the tray open, Console closed and open', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      await app.gotoRoute('/');
      await openTray(page);
      await app.settle();
      await expectAxeClean(page, { key: { route: 'home', state: 'followups-tray' }, theme, known: KNOWN_VIOLATIONS });

      // A click outside the tray (the Console toggle) closes it: open the
      // Console first, then the tray again.
      await page.keyboard.press('Escape');
      await expect(page.locator('.followups')).toHaveCount(0);
      await app.openConsole();
      await openTray(page);
      await app.settle();
      // Scoped: the Console's own audit-panel defect belongs to W5e w5-compiler-lint.
      await expectAxeClean(page, { key: { route: 'home', state: 'followups-tray' }, theme, known: KNOWN_VIOLATIONS, include: '.rail' });
    });

    test('(c) no sideways overflow, a 1280x720 fit, and the tray on top of the Console sheet at 1152x720', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      await app.gotoRoute('/');
      const { tray } = await openTray(page);
      expect(await overflowsSideways(page)).toBe(false);

      await page.setViewportSize({ width: 1280, height: 720 });
      await app.settle();
      const box = await tray.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.y).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(1280);
      expect(box!.y + box!.height).toBeLessThanOrEqual(720);
      expect(await overflowsSideways(page)).toBe(false);

      await page.keyboard.press('Escape');
      await expect(tray).toHaveCount(0);
      await page.setViewportSize({ width: 1152, height: 720 });
      await app.openConsole();
      await openTray(page, { keyboard: true });
      const centre = await tray.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return hit !== null && element.contains(hit);
      });
      expect(centre, 'the tray, not the Console sheet, is under its own centre').toBe(true);
    });

    test('(d) a slow snooze keeps the row loading, sends a closed body, then the row leaves', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      const bodies: unknown[] = [];
      let release: () => void = () => undefined;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      mockApi.register('POST', '/api/leads/:id/snooze', async (request) => {
        bodies.push(request.body);
        await gate;
        return json({ borrower_id: request.params.id, wake_at: '2026-07-17T15:00:00Z', wake_clamped: false, audit_event_id: '0f0f0f0f-0000-4000-8000-00000000f011' });
      });
      await app.gotoRoute('/');
      const { tray } = await openTray(page);
      const row = tray.locator(`.followups__row[data-borrower-id="${FOLLOWUP_IDS.today}"]`);
      await row.getByRole('button', { name: 'Snooze' }).click();
      const group = row.getByRole('group', { name: `Snooze ${FOLLOWUP_IDS.today} for` });
      await expect(group.getByRole('button', { name: '1 day' })).toBeFocused();
      await group.getByRole('button', { name: '3 days' }).click();

      await expect(group.getByRole('button', { name: '3 days' })).toHaveAttribute('aria-busy', 'true');
      await expect(group.getByRole('button', { name: '1 day' })).toBeDisabled();
      await expect(row, 'pessimistic: the row stays while the snooze is in flight').toBeVisible();
      await expect.poll(() => bodies.length).toBe(1);
      expect(Object.keys(bodies[0] as object).sort()).toEqual(['request_id', 'snooze_days']);
      expect((bodies[0] as { snooze_days: number }).snooze_days).toBe(3);

      release();
      await expect(row).toHaveCount(0);
      await expect(page.locator('.toast').filter({ hasText: 'Snoozed until' })).toBeVisible();
    });

    test('(d) after a snooze, focus moves to the next visible row, else to the tray heading', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      await app.gotoRoute('/');
      const { tray } = await openTray(page);
      const snoozeRow = async (borrowerId: string) => {
        const row = tray.locator(`.followups__row[data-borrower-id="${borrowerId}"]`);
        await row.getByRole('button', { name: 'Snooze' }).click();
        await row.getByRole('button', { name: '1 day' }).click();
        await expect(row).toHaveCount(0);
      };

      // Control: the next row sits in an open section and takes focus.
      await snoozeRow(FOLLOWUP_IDS.today);
      await expect(tray.locator(`.followups__row[data-borrower-id="${FOLLOWUP_IDS.week}"] .followups__id`)).toBeFocused();

      // The last open row: Older and No longer contactable stay collapsed
      // after it, and a link inside a closed <details> cannot take focus.
      await snoozeRow(FOLLOWUP_IDS.assigned);
      await expect(tray.locator('details.followups__section--collapsed:not([open])')).toHaveCount(2);
      await expect(tray.getByRole('heading', { name: 'Follow-ups', level: 2 })).toBeFocused();
      expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
    });

    test('(d) a 409 not_contactable keeps the row and re-reads the list', async ({ app, hygiene, mockApi, page }) => {
      hygiene.allow('console.error', /status of 409/);
      registerFollowUps(mockApi);
      mockApi.register('POST', '/api/leads/:id/snooze', () => json(SNOOZE_NOT_CONTACTABLE_409, { status: 409 }));
      await app.gotoRoute('/');
      const { tray } = await openTray(page);
      await app.settle();
      const before = listReads(mockApi);
      const row = tray.locator(`.followups__row[data-borrower-id="${FOLLOWUP_IDS.today}"]`);
      await row.getByRole('button', { name: 'Snooze' }).click();
      await row.getByRole('button', { name: '1 week' }).click();

      await expect(page.locator('.toast').filter({ hasText: 'This lead can no longer be contacted.' })).toBeVisible();
      await expect.poll(() => listReads(mockApi)).toBeGreaterThan(before);
      await expect(row).toBeVisible();
    });

    test('(e) the ineligible default actor gets no item and no list read', async ({ app, mockApi, page }) => {
      await app.gotoRoute('/');
      await app.settle();
      const summaries = mockApi.calls.filter((call) => normalizeApiPath(call.path) === '/api/sales/followups/summary');
      expect(summaries.length, 'non-vacuity: the poll ran').toBeGreaterThan(0);
      await expect(page.locator(TRIGGER)).toHaveCount(0);
      expect(listReads(mockApi)).toBe(0);
    });
  });
}
