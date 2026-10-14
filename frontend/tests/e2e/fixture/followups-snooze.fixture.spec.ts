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
 *  (e) the default (ineligible) actor gets no item and no list read;
 *  (f) one row grammar in every row, short due text or long, contactable or
 *      withheld: the reason line (id, then the reason), then a meta line
 *      that STARTS with the due time, Snooze after it on the same line (a
 *      withheld row's closed reason below it); each reason qualifier (the
 *      owner name, "attempt N", "new signal") renders on one line. Measured
 *      under LINUX_TEXT_EMULATION off Linux;
 *  (g) while the tray is open its trigger keeps the rail's current-module
 *      fill, edge and ink (aria-expanded), pointer elsewhere, without the
 *      location bar, and drops it when the tray closes; under forced colours
 *      (its fill forced away) the open edge alone paints Highlight.
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { FOLLOWUP_IDS, registerFollowUps, SNOOZE_NOT_CONTACTABLE_409 } from './data/followUps';
import { json, normalizeApiPath, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const TRIGGER = '.rail button.rail__item:has(.mod:text-is("Due"))';
/**
 * Linux Chromium draws Geist Mono about 6% wider (the LINUX_TEXT_EMULATION
 * pattern of geo-foundation.fixture.spec.ts): off Linux, widen the tray's mono
 * ids, so the reason beside them gets the width CI gives it.
 */
const LINUX_TEXT_EMULATION = '.followups .followups__id { letter-spacing: 0.5px; }';

interface RowGeometry {
  borrower: string;
  due: string;
  withheld: boolean;
  /** The due time's left edge minus the row's content-box start. */
  dueInset: number;
  /** The due time's top minus the reason line's bottom (the id and the reason). */
  dueBelowReason: number;
  /** Snooze's left edge minus the due time's right edge (null: no Snooze). */
  snoozeAfterDue: number | null;
  /** Snooze overlaps the due time's line box vertically (null: no Snooze). */
  snoozeOnDueLine: boolean | null;
  /** The closed reason's top minus the due time's bottom (null: contactable). */
  blockBelowDue: number | null;
}

/** Every row's boxes, collapsed groups included (open them first). */
async function rowGeometry(tray: Locator): Promise<RowGeometry[]> {
  return tray.evaluate((element) =>
    [...element.querySelectorAll<HTMLElement>('.followups__row')].map((row) => {
      const box = (selector: string) => row.querySelector(selector)?.getBoundingClientRect() ?? null;
      const id = box('.followups__id');
      const why = box('.followups__why');
      const due = box('.followups__due');
      if (!id || !why || !due) throw new Error(`row ${row.dataset.borrowerId} lacks its id, reason or due time`);
      const snooze = box('button.followups__snooze');
      const block = box('.followups__block');
      const start = row.getBoundingClientRect().left + parseFloat(getComputedStyle(row).paddingLeft);
      return {
        borrower: row.dataset.borrowerId ?? '',
        due: row.querySelector('.followups__due')?.textContent ?? '',
        withheld: block !== null,
        dueInset: due.left - start,
        dueBelowReason: due.top - Math.max(id.bottom, why.bottom),
        snoozeAfterDue: snooze ? snooze.left - due.right : null,
        snoozeOnDueLine: snooze ? snooze.top < due.bottom && snooze.bottom > due.top : null,
        blockBelowDue: block ? block.top - due.bottom : null,
      };
    }),
  );
}

/** How many line boxes `phrase` occupies inside the row's reason (-1: not found). */
async function phraseLines(row: Locator, phrase: string): Promise<number> {
  return row.locator('.followups__why').evaluate((why, text) => {
    const walker = document.createTreeWalker(why, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    const starts: number[] = [];
    let flat = '';
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      starts.push(flat.length);
      nodes.push(node);
      flat += node.data;
    }
    const at = flat.indexOf(text);
    if (at < 0) return -1;
    const locate = (offset: number): [Text, number] => {
      let index = nodes.length - 1;
      while (index > 0 && starts[index] > offset) index -= 1;
      return [nodes[index], offset - starts[index]];
    };
    const range = document.createRange();
    range.setStart(...locate(at));
    range.setEnd(...locate(at + text.length));
    return new Set([...range.getClientRects()].filter((rect) => rect.width > 0).map((rect) => Math.round(rect.top))).size;
  }, phrase);
}

interface TriggerPaint {
  background: string;
  border: string;
  color: string;
  bar: string;
}

async function triggerPaint(locator: Locator): Promise<TriggerPaint> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      background: style.backgroundColor,
      border: style.borderTopColor,
      color: style.color,
      bar: getComputedStyle(element, '::before').content,
    };
  });
}

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

    test('(f) every row reads its reason, then a meta line that starts with the due time', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      await app.gotoRoute('/');
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
      const { tray } = await openTray(page);
      // Older and No longer contactable are collapsed: open them (a <summary>
      // click inside the tray reads nothing and keeps it open).
      for (const summary of await tray.locator('details.followups__section--collapsed > summary').all()) await summary.click();
      await expect(tray.locator('details.followups__section--collapsed[open]')).toHaveCount(2);
      await app.settle();

      const rows = await rowGeometry(tray);
      console.log(`[followups-rows] ${JSON.stringify(rows)}`);
      expect(rows).toHaveLength(9);
      // Non-vacuity: the shortest due text (the Today row's) and the long ones
      // both run, and so do contactable and withheld rows.
      expect(rows.map((row) => row.due)).toEqual(expect.arrayContaining(['in 5 hr.', '2 days ago', 'yesterday']));
      expect(rows.filter((row) => row.withheld)).toHaveLength(3);
      for (const row of rows) {
        const label = `${row.borrower} (${row.due})`;
        expect.soft(Math.abs(row.dueInset), `${label}: the due time starts the meta line`).toBeLessThanOrEqual(0.5);
        expect.soft(row.dueBelowReason, `${label}: the meta line sits below the reason line`).toBeGreaterThanOrEqual(0);
        if (row.withheld) {
          expect.soft(row.snoozeAfterDue, `${label}: a withheld row offers no Snooze`).toBeNull();
          expect.soft(row.blockBelowDue ?? -1, `${label}: the closed reason sits below the meta line`).toBeGreaterThanOrEqual(0);
        } else {
          expect.soft(row.snoozeOnDueLine, `${label}: Snooze shares the due time's line`).toBe(true);
          expect.soft(row.snoozeAfterDue ?? -1, `${label}: Snooze follows the due time`).toBeGreaterThan(0);
        }
      }
      // One due column: every due time starts at the same x.
      expect.soft(new Set(rows.map((row) => Math.round(row.dueInset))).size, 'every due time starts at one x').toBe(1);

      // Each qualifier is one phrase: the owner name never breaks inside itself.
      const row = (borrowerId: string) => tray.locator(`.followups__row[data-borrower-id="${borrowerId}"]`);
      for (const [borrowerId, phrase] of [
        [FOLLOWUP_IDS.requested, 'Summit LO 02'],
        [FOLLOWUP_IDS.week, 'attempt 1'],
        [FOLLOWUP_IDS.week, 'new signal'],
        [FOLLOWUP_IDS.overdue, 'attempt 2'],
      ] as const) {
        expect.soft(await phraseLines(row(borrowerId), phrase), `${borrowerId}: "${phrase}" renders on one line`).toBe(1);
      }
    });

    test('(g) the open tray keeps its trigger in the rail active state, without the location bar', async ({ app, mockApi, page }) => {
      registerFollowUps(mockApi);
      await app.gotoRoute('/');
      const trigger = page.locator(TRIGGER);
      await expect(trigger).toBeVisible();
      await app.settle();
      const closed = await triggerPaint(trigger);
      const current = await triggerPaint(page.locator('.rail a.rail__item.is-active'));
      expect(current.bar, 'non-vacuity: the current module draws its location bar').not.toBe('none');
      expect(closed.background, 'at rest the trigger has no fill').toBe('rgba(0, 0, 0, 0)');

      // Forced colours force the fill away. Chromium paints the open state's
      // --chip-line edge Highlight; at rest (unfocused) the edge is the forced
      // ink, never Highlight. Measured unfocused: a focus-visible trigger
      // takes a Highlight edge whatever its state.
      await page.emulateMedia({ forcedColors: 'active' });
      const highlight = await page.evaluate(() => {
        const probe = document.body.appendChild(document.createElement('span'));
        probe.style.color = 'Highlight';
        const value = getComputedStyle(probe).color;
        probe.remove();
        return value;
      });
      expect(await trigger.evaluate((element) => element === document.activeElement), 'measured unfocused').toBe(false);
      expect((await triggerPaint(trigger)).border, 'forced colours: no Highlight edge at rest').not.toBe(highlight);
      await page.emulateMedia({ forcedColors: 'none' });

      const { tray } = await openTray(page);
      // Pointer off the trigger: the open state, not :hover, is measured.
      const title = await tray.locator('.followups__title').boundingBox();
      if (!title) throw new Error('the tray title has no box');
      await page.mouse.move(title.x + title.width / 2, title.y + title.height / 2);
      expect(await trigger.evaluate((element) => element.matches(':hover'))).toBe(false);
      await expect
        .poll(() => triggerPaint(trigger), { message: 'the open trigger wears the current-module fill, edge and ink, no bar' })
        .toEqual({ background: current.background, border: current.border, color: current.color, bar: 'none' });
      await page.emulateMedia({ forcedColors: 'active' });
      await expect.poll(async () => (await triggerPaint(trigger)).border, { message: 'forced colours: a Highlight edge' }).toBe(highlight);
      await page.emulateMedia({ forcedColors: 'none' });

      await page.keyboard.press('Escape');
      await expect(tray).toHaveCount(0);
      await expect(trigger).toHaveAttribute('aria-expanded', 'false');
      await expect.poll(() => triggerPaint(trigger), { message: 'closed again, the trigger is at rest' }).toEqual(closed);
    });
  });
}
