/**
 * Focus Not Obscured (WCAG 2.4.11, a11y-v2) across engines: the Lead Queue
 * clearance walks of lead-queue.fixture.spec.ts (b), ported to focus()-driven
 * walks (fixture/focusWalk.ts) so they prove the same thing in WebKit, whose
 * Tab skips buttons and checkboxes on macOS/Linux defaults (manual check
 * 2026-09-30). Runs in fixture-chromium on every fixture run and in
 * fixture-webkit in the e2e-cross-engine CI job (MIP_CROSS_ENGINE=1), at
 * 1440x900. Every clearance test has its non-vacuity twin (the same walk with
 * the clearance's CSS zeroed finds a covered stop).
 *
 * WebKit defect found by the 2026-09-30 manual check: element.focus() on a
 * pinned (position: sticky, inline-end) Approve / Reject scrolled .tbl-wrap
 * to its end (scrollLeft 0 -> 284 with the Console open); the pinned
 * controls' scroll-margin stops it only in Chromium. w5-approval-core's guard
 * in useTableScrollClearance.ts restores the offset after WebKit's reveal,
 * and the invariance test below now runs in both engines.
 */
import type { Locator, Page } from '@playwright/test';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { DNC_LEAD, registerQueueLayoutLeads } from './data/queueLayout';
import {
  FOCUS_RING_ROOM,
  ZERO_NAV_MARGIN,
  ZERO_PIN_MARGIN,
  ZERO_TABLE_BLOCK_MARGIN,
  ZERO_TABLE_CLEARANCE,
  addScrollRoom,
  behindRouteNav,
  focusStop,
  focusWalk,
  gapBelowRouteNav,
  rowBehindHeader,
  settleFrames,
  tableUpUnderRouteNav,
  tableWrap,
  underPinnedColumn,
} from './focusWalk';
import { expect, test } from './test';

const ELIGIBLE = PRIMARY_BORROWER.borrower_id;

async function expand(page: Page, borrowerId: string): Promise<void> {
  const toggle = page.getByRole('button', { name: `Toggle preview for lead ${borrowerId}` });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
}

async function focusAndSettle(page: Page, target: Locator): Promise<void> {
  await target.focus();
  await expect(target).toBeFocused();
  await settleFrames(page);
}

/** K from the walk's start, `presses` times: the cursor row's gap below the nav after each move. */
async function cursorWalkUp(page: Page, presses: number): Promise<number[]> {
  await focusAndSettle(page, await tableUpUnderRouteNav(page));
  const cursor = page.locator('table.tbl tr.is-cursor');
  let previous = await cursor.getAttribute('data-borrower-row');
  expect(previous, 'precondition: the focus put the cursor on the start row').toBeTruthy();
  const gaps: number[] = [];
  for (let index = 0; index < presses; index += 1) {
    await page.keyboard.press('k');
    await expect(cursor, 'K moved the cursor').not.toHaveAttribute('data-borrower-row', previous ?? '');
    await settleFrames(page);
    previous = await cursor.getAttribute('data-borrower-row');
    gaps.push(await gapBelowRouteNav(cursor));
  }
  return gaps;
}

async function consoleOverflow(page: Page): Promise<number> {
  return tableWrap(page).evaluate((wrap) => wrap.scrollWidth - wrap.clientWidth);
}

/**
 * Focus each pinned control of `borrowerId`'s row with the table at `start`
 * and return scrollLeft after the focus has settled (two frames).
 */
async function settledScrollLeftAfterPinnedFocus(page: Page, borrowerId: string, start: number): Promise<number[]> {
  const ends: number[] = [];
  for (const id of [`lead-approve-${borrowerId}`, `lead-reject-${borrowerId}`]) {
    // Let the scroller's own scroll event land before the focus, as a person's
    // scroll would: useTableScrollClearance records the offset there, and a
    // script scroll in the same frame as a pinned focus is its documented residual.
    await tableWrap(page).evaluate(async (wrap, left) => {
      wrap.scrollLeft = left;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, start);
    await focusAndSettle(page, page.getByTestId(id));
    ends.push(await tableWrap(page).evaluate((wrap) => wrap.scrollLeft));
  }
  return ends;
}

/**
 * Where each pinned control's focus sends scrollLeft, read where no restore
 * can hide it (integrator correction C1): `sync` is read inside the same
 * evaluate that calls focus() (Chromium reveals synchronously); `peak` is the
 * largest scrollLeft a window capture-phase scroll listener sees in the next
 * two frames (WebKit reveals at the next rendering update, so its sync read is
 * always the start). A capture listener on window runs before any listener on
 * the scroller itself, so a scroll-event or rAF restore cannot hide the peak.
 */
async function scrollLeftReachedByPinnedFocus(page: Page, borrowerId: string, start: number): Promise<Array<{ sync: number; peak: number }>> {
  return tableWrap(page).evaluate(async (wrap, { id, left }) => {
    const reached: Array<{ sync: number; peak: number }> = [];
    for (const testId of [`lead-approve-${id}`, `lead-reject-${id}`]) {
      (document.activeElement as HTMLElement | null)?.blur();
      wrap.scrollLeft = left;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const control = wrap.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
      if (!control) throw new Error(`no ${testId}`);
      let peak = wrap.scrollLeft;
      const onScroll = (event: Event) => { if (event.target === wrap) peak = Math.max(peak, wrap.scrollLeft); };
      window.addEventListener('scroll', onScroll, { capture: true });
      // Sample every frame too, from a rAF chain queued BEFORE focus(): in each
      // frame it runs ahead of any rAF a focus handler queues (a restore).
      let frames = 0;
      const sampled = new Promise<void>((resolve) => {
        const sample = () => {
          peak = Math.max(peak, wrap.scrollLeft);
          frames += 1;
          if (frames >= 6) resolve();
          else requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      control.focus();
      const sync = wrap.scrollLeft;
      await sampled;
      window.removeEventListener('scroll', onScroll, { capture: true });
      reached.push({ sync, peak: Math.max(peak, sync) });
    }
    return reached;
  }, { id: borrowerId, left: start });
}

/** Zeroes the sticky header controls' clearance (LeadTable.css, scroll-state query). */
const ZERO_HEADER_MARGIN = '.lead-table__table thead * { scroll-margin-block-start: 0px !important; }';

/** The table at scrollTop `tableTop`, and `.main` scrolled so the sticky header row sits behind the route nav. */
async function headerUnderRouteNav(page: Page, tableTop: number): Promise<void> {
  await addScrollRoom(page);
  const placed = await page.locator('.main').evaluate((main, top) => {
    const nav = main.querySelector<HTMLElement>('.route-nav');
    const wrap = main.querySelector<HTMLElement>('.tbl-wrap');
    const head = wrap?.querySelector<HTMLElement>('thead th');
    if (!nav || !wrap || !head) throw new Error('no route nav or table header');
    wrap.scrollTop = top;
    // The header row (56.5px at 1440x900) is about the nav's height (57px): align the tops.
    main.scrollTop += Math.ceil(head.getBoundingClientRect().top - nav.getBoundingClientRect().top);
    const box = head.getBoundingClientRect();
    const band = nav.getBoundingClientRect();
    return { covered: box.top >= band.top - 1 && box.bottom <= band.bottom + 1, wrapTop: wrap.scrollTop };
  }, tableTop);
  expect(placed.covered, 'precondition: the sticky header sits behind the route nav').toBe(true);
  expect(Math.abs(placed.wrapTop - tableTop), 'precondition: the table sits at its offset').toBeLessThanOrEqual(1);
}

function headerControls(page: Page): Record<string, Locator> {
  return {
    'a sort control': page.locator('.lead-table__table thead th[aria-sort] button').first(),
    'select-all': page.locator('.lead-table__table thead input[type="checkbox"]').first(),
  };
}

test.describe('focus() walks never stop under sticky chrome (a11y-v2)', () => {
  for (const zeroed of [false, true]) {
    // WebKit 26 has no scroll-state container queries: useTableScrollClearance's
    // data-at-block-start flag carries the same clearance there (a11y-v2).
    test(`${zeroed ? 'non-vacuity, header margin zeroed: ' : ''}with the table at its start, sort and select-all focused from under the route nav ${zeroed ? 'stay there' : 'stop below it'}`, async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      if (zeroed) await page.addStyleTag({ content: ZERO_HEADER_MARGIN });
      for (const [name, control] of Object.entries(headerControls(page))) {
        await headerUnderRouteNav(page, 0);
        await focusAndSettle(page, control);
        expect(await tableWrap(page).evaluate((wrap) => wrap.scrollTop), `${name}: the table does not move`).toBeLessThanOrEqual(1);
        if (zeroed) expect(await gapBelowRouteNav(control), `${name}: a stop under the nav`).toBeLessThan(0);
        else expect(await gapBelowRouteNav(control), `${name}: below the nav`).toBeGreaterThanOrEqual(-0.5);
      }
    });
  }

  for (const restored of [false, true]) {
    test(`${restored ? 'non-vacuity, nav margin restored inside it: ' : ''}a control in view in the approval review dialog takes focus ${restored ? 'and the dialog over-scrolls' : 'without the dialog scrolling'}`, async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      await page.getByTestId(`lead-approve-${ELIGIBLE}`).click();
      const dialog = page.locator('dialog.lead-approve-dialog[open]');
      await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeEnabled();
      // A 200px dialog with scroll room after its controls, scrolled so a control
      // sits 4px below the dialog's top edge; the nav's margin (61px) would scroll it.
      const style = `dialog.lead-approve-dialog { max-block-size: 200px !important; }${restored ? ' .main .lead-approve-dialog * { scroll-margin-block-start: var(--nav-clear) !important; }' : ''}`;
      await page.addStyleTag({ content: style });
      const moved = await dialog.evaluate(async (scroller) => {
        const frames = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const room = document.createElement('div');
        room.style.blockSize = '400px';
        scroller.append(room);
        const control = [...scroller.querySelectorAll<HTMLElement>('button:not(:disabled), textarea, input:not([type="hidden"]), select, a[href]')]
          // Not the one the dialog focused on open: focus() on it would not move focus.
          .filter((el) => el.getClientRects().length > 0 && el !== document.activeElement)
          .at(-1);
        if (!control) throw new Error('precondition: a control in the dialog');
        // Linux WebKit can still be settling the dialog's layout after `room`
        // lands (a later frame moves the control). Re-place until two
        // consecutive frame pairs report the same rects, bounded; the 20px
        // precondition stays (the twin's >40px over-scroll needs it, 61px margin).
        const read = () => {
          const port = scroller.getBoundingClientRect();
          const box = control.getBoundingClientRect();
          return { top: scroller.scrollTop, portTop: port.top, portBottom: port.bottom, boxTop: box.top, boxBottom: box.bottom };
        };
        const same = (a: ReturnType<typeof read>, b: ReturnType<typeof read>) =>
          (Object.keys(a) as Array<keyof typeof a>).every((key) => Math.abs(a[key] - b[key]) < 0.5);
        const placed = (r: ReturnType<typeof read>) => r.boxTop >= r.portTop && r.boxBottom <= r.portBottom && r.boxTop - r.portTop < 20;
        let attempts = 0;
        let settled = false;
        let last = read();
        while (attempts < 12 && !settled) {
          attempts += 1;
          scroller.scrollTop += last.boxTop - last.portTop - 4;
          await frames();
          const first = read();
          await frames();
          last = read();
          settled = same(first, last) && placed(last);
        }
        const before = scroller.scrollTop;
        control.focus();
        await frames();
        const delta = Math.abs(scroller.scrollTop - before);
        room.remove();
        const metrics = `${attempts} placement(s), settled ${settled}; scrollTop ${before} of ${scroller.scrollHeight - scroller.clientHeight}; `
          + `control ${(last.boxTop - last.portTop).toFixed(1)}..${(last.boxBottom - last.portTop).toFixed(1)} in ${(last.portBottom - last.portTop).toFixed(1)}`;
        return { inView: settled, delta, focused: document.activeElement === control, metrics };
      });
      expect(moved.inView, `precondition: the control sits within 20px of the dialog's top, in full view, on stable rects (${moved.metrics})`).toBe(true);
      expect(moved.focused).toBe(true);
      if (restored) expect(moved.delta, `the nav margin over-scrolls the dialog (${moved.metrics})`).toBeGreaterThan(40);
      else expect(moved.delta, `the dialog does not move (${moved.metrics})`).toBeLessThanOrEqual(1);
    });
  }

  test('with the table scrolled, a header control focus leaves the table where it was (no "reveal" of a stuck header)', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    for (const [name, control] of Object.entries(headerControls(page))) {
      await headerUnderRouteNav(page, 200);
      await focusAndSettle(page, control);
      expect(Math.abs((await tableWrap(page).evaluate((wrap) => wrap.scrollTop)) - 200), `${name}: scrollTop stays`).toBeLessThanOrEqual(1);
    }
  });

  for (const route of ['/lead-queue', '/segment-intelligence'] as const) {
    test(`${route}: with .main at its end, a reverse walk up the rows stops clear of the route nav`, async ({ app, page }) => {
      await app.gotoRoute(route);
      await focusAndSettle(page, await tableUpUnderRouteNav(page));
      const stops = await focusWalk(page, 'reverse', 30);
      expect(stops.length, 'non-vacuity: the walk moved focus').toBeGreaterThan(20);
      expect(stops.filter((stop) => stop.covered.length > 0)).toEqual([]);
    });

    test(`${route} non-vacuity: with the table's block margin zeroed, the same walk stops under the route nav`, async ({ app, page }) => {
      await app.gotoRoute(route);
      await page.addStyleTag({ content: ZERO_TABLE_BLOCK_MARGIN });
      await focusAndSettle(page, await tableUpUnderRouteNav(page));
      const stops = await focusWalk(page, 'reverse', 30);
      expect(stops.filter((stop) => stop.covered.includes('route nav')).length, 'a stop under the nav').toBeGreaterThan(0);
    });
  }

  test('/lead-queue: with .main at its end, K brings the cursor row up clear of the route nav', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    expect((await cursorWalkUp(page, 8)).filter((gap) => gap < -0.5)).toEqual([]);
  });

  test('/lead-queue non-vacuity: with the table\'s block margin zeroed, K leaves a cursor row under the route nav', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await page.addStyleTag({ content: ZERO_TABLE_BLOCK_MARGIN });
    expect((await cursorWalkUp(page, 8)).filter((gap) => gap < -0.5).length, 'a cursor row under the nav').toBeGreaterThan(0);
  });

  test('a reverse walk up the rows stops clear of the sticky header, with a selection bar up', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await page.getByTestId(`lead-select-${LEADS[0].borrower_id}`).check();
    await page.getByTestId(`lead-select-${LEADS[2].borrower_id}`).check();
    await expect(page.getByTestId('lead-bulk-actions')).toBeVisible();
    const hidden = await rowBehindHeader(page, 5);
    await focusAndSettle(page, page.getByTestId(`lead-select-${LEADS[6].borrower_id}`));

    const stops = await focusWalk(page, 'reverse', 60);
    expect(stops.length, 'non-vacuity: the walk moved focus').toBeGreaterThan(40);
    expect(stops.filter((stop) => stop.covered.length > 0)).toEqual([]);

    await rowBehindHeader(page, 5);
    await focusAndSettle(page, page.getByTestId(`lead-select-${hidden}`));
    expect((await focusStop(page))?.covered, 'a control behind the header scrolls clear of it').toEqual([]);
  });

  test('with the Console open, a walk across the eligible row reaches Approve / Reject, never under the pinned column', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    expect(await consoleOverflow(page), 'precondition: the Console narrows the table into a horizontal scroll').toBeGreaterThan(0);
    await focusAndSettle(page, page.getByTestId(`lead-select-${ELIGIBLE}`));

    const stops = await focusWalk(page, 'forward', 10);
    expect(stops.some((stop) => stop.label === `Approve ${ELIGIBLE}`), 'the walk reached the Approval cell').toBe(true);
    expect(stops.filter((stop) => stop.covered.length > 0)).toEqual([]);

    // The Status cell's `+n`, the last control before the pin, parked under it.
    const more = page.locator(`[data-testid="lead-status-${DNC_LEAD.borrower_id}"] .lead-table__more`);
    await underPinnedColumn(more);
    await focusAndSettle(page, more);
    expect((await focusStop(page))?.covered, 'a control under the pin scrolls clear of it').toEqual([]);
  });

  test('non-vacuity: with the table\'s clearances zeroed, the same stops are covered', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await page.addStyleTag({ content: ZERO_TABLE_CLEARANCE });
    const hidden = await rowBehindHeader(page, 5);
    await focusAndSettle(page, page.getByTestId(`lead-select-${hidden}`));
    expect((await focusStop(page))?.covered, 'a stop under the header').toContain('thead');

    await app.openConsole();
    const more = page.locator(`[data-testid="lead-status-${DNC_LEAD.borrower_id}"] .lead-table__more`);
    await underPinnedColumn(more);
    await focusAndSettle(page, more);
    expect((await focusStop(page))?.covered, 'a stop under the pin').toContain('pinned column');
  });

  for (const zeroed of [false, true]) {
    test(`${zeroed ? 'non-vacuity, nav margin zeroed: ' : ''}a /glossary control focused from behind the route nav ${zeroed ? 'stays there' : 'stops below it'}`, async ({ app, page }) => {
      await app.gotoRoute('/glossary');
      if (zeroed) await page.addStyleTag({ content: ZERO_NAV_MARGIN });
      const target = page.locator('.main .proto-hero').getByRole('link', { name: 'Back to leads' });
      await behindRouteNav(page, target);
      await focusAndSettle(page, target);
      if (zeroed) expect(await gapBelowRouteNav(target), 'a stop under the nav').toBeLessThan(0);
      else expect(await gapBelowRouteNav(target), 'the focused control sits below the nav').toBeGreaterThanOrEqual(-0.5);
    });

    test(`${zeroed ? 'non-vacuity, block margin zeroed: ' : ''}the expanded row's Approve focused from behind the route nav ${zeroed ? 'stays there' : 'stops below it'}`, async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      if (zeroed) await page.addStyleTag({ content: ZERO_TABLE_BLOCK_MARGIN });
      await expand(page, ELIGIBLE);
      const approve = page.getByTestId(`lead-row-approval-${ELIGIBLE}`).getByRole('button', { name: 'Approve outreach' });
      await behindRouteNav(page, approve);
      await focusAndSettle(page, approve);
      if (zeroed) expect(await gapBelowRouteNav(approve), 'a stop under the nav').toBeLessThan(0);
      else expect(await gapBelowRouteNav(approve), 'the focused control sits below the nav').toBeGreaterThanOrEqual(-0.5);
    });
  }

  for (const tab of ['ask', 'workflows'] as const) {
    test(`/ask-genie's ${tab} tab clears the nav once`, async ({ app, page }) => {
      await app.gotoRoute(tab === 'ask' ? '/ask-genie' : '/ask-genie?tab=workflows');
      const panel = page.locator('section[role="tabpanel"]:not([hidden])');
      if (tab === 'ask') await expect(panel.locator('.genie-composer'), 'precondition: the Ask tab').toBeVisible();
      else await expect(panel.locator('.genie-composer'), 'precondition: the Workflows tab').toHaveCount(0);
      await addScrollRoom(page);
      const target = tab === 'ask' ? panel.locator('button:not(.genie-composer *)').first() : panel.getByRole('button').first();
      await target.evaluate((element) => element.scrollIntoView({ block: 'start' }));
      const gap = await gapBelowRouteNav(target);
      expect(gap, 'at or below the nav').toBeGreaterThanOrEqual(-0.5);
      expect(gap, 'the focus ring\'s room, not a second nav height').toBeLessThanOrEqual(FOCUS_RING_ROOM + 1);
    });
  }

  test('a focus on a pinned Approve or Reject leaves the table\'s scrollLeft where it was, Console open', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    const overflow = await consoleOverflow(page);
    expect(overflow, 'precondition: the Console narrows the table into a horizontal scroll').toBeGreaterThan(40);
    for (const start of [0, Math.round(overflow / 2)]) {
      for (const end of await settledScrollLeftAfterPinnedFocus(page, ELIGIBLE, start)) {
        expect(Math.abs(end - start), `a pinned control focused at scrollLeft ${start} stays there`).toBeLessThanOrEqual(1);
      }
    }
    // The tabbable before the next row's checkbox is this row's Reject, in the pin.
    await tableWrap(page).evaluate((wrap) => { wrap.scrollLeft = 0; });
    const order = await page.locator('tr[data-borrower-row]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-borrower-row')));
    const next = order[order.indexOf(ELIGIBLE) + 1];
    expect(next, 'precondition: a row follows').toBeTruthy();
    await focusAndSettle(page, page.getByTestId(`lead-select-${next}`));
    const back = await focusWalk(page, 'reverse', 1);
    expect(back.map((stop) => stop.label)).toEqual([`Reject ${ELIGIBLE}`]);
    expect(await tableWrap(page).evaluate((wrap) => wrap.scrollLeft), 'the row\'s borrower id stays in view').toBeLessThanOrEqual(1);
  });

  test('non-vacuity: with the pinned controls\' margin zeroed, a focus on one scrolls the table to its end', async ({ app, browserName, mockApi, page }) => {
    test.skip(
      browserName === 'webkit' && process.platform === 'linux',
      'Linux WebKit never reveals a focused pinned control (the defect is macOS WebKit\'s; W5a CI reached 0), so this twin cannot be non-vacuous there; the invariance test above still runs in every engine',
    );
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    await page.addStyleTag({ content: ZERO_PIN_MARGIN });
    const overflow = await consoleOverflow(page);
    expect(overflow).toBeGreaterThan(40);
    for (const { sync, peak } of await scrollLeftReachedByPinnedFocus(page, ELIGIBLE, 0)) {
      // Chromium: the synchronous read (C1); WebKit reveals a frame later, so its peak.
      const reached = browserName === 'webkit' ? peak : sync;
      expect(reached, 'the scroller\'s inline-end padding "reveals" a control already in view').toBeGreaterThan(overflow - 2);
    }
  });
});
