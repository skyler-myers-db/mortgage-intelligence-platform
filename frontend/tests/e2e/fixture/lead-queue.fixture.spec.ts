/**
 * Rendered-layer proofs for the wave-4b Lead Queue lane (w4-lead-queue) at
 * the contracted 1440x900 viewport. Colour and layout checks run in dark and
 * light.
 *
 *  (a) The expanded row's approval banner (tables-01): shown for an eligible
 *      row with the exact copy; expanding reads and drafts nothing; its
 *      Approve opens the inline review with exactly the review's one draft;
 *      absent while that review is open, after a receipt, and on terminal
 *      or non-actionable rows; visible and disabled for a gated approver;
 *      its Approve is the topmost element at its centre with the Console
 *      closed and open.
 *  (a) also: the banner's copy keeps its width beside the icon, title on one
 *      line, and its actions wrap under it, Console closed and open.
 *  (b) The focus-obscured walk (a11y-v2): no focus stop's box is fully
 *      covered by the sticky thead, the pinned Approval column, the bulk
 *      bar or the sticky route nav; with the table's clearances zeroed the
 *      same walk does find a covered stop. With `.main` at its natural
 *      end (1440x900, no spacer) the rows run up under the route nav: a
 *      Shift+Tab walk up them (/lead-queue and /segment-intelligence) and
 *      K's cursor row stop below it, as does the expanded row's Approve
 *      focused from behind the nav; none do with the table's block margin
 *      zeroed. A control focused from behind the sticky route nav
 *      (/glossary) stops below it, and not with the scroll-margin zeroed;
 *      /ask-genie's Ask tab clears the nav once (its own padding), its
 *      Workflows tab through 38-focus-clearance.css. A focus on a pinned
 *      Approve / Reject leaves the table's scrollLeft where it was (the
 *      pin's own controls are not "revealed"), and a pointer pick in a
 *      scrolled STATE menu writes the option aimed at (the nav's margin
 *      stays out of the in-place listbox); each with its non-vacuity twin.
 *      The clearance's style cost: focus-clearance-cost.fixture.spec.ts.
 *  (d) The assignment outcome (critic-06): Escape and Cancel make no POST
 *      and hand focus back; Record needs its confirm row, then one POST; a
 *      409 keeps the stage and shows the error.
 *  (e) The DNC source and the eligibility source are visible text (critic-08).
 *  (f) A load and a PUSH naming row 140 of the 160-row virtualized queue put
 *      that row inside the scrollport (tables-09 follow-up).
 *  (g) Tab to row B's checkbox with the cursor on A, then A: B's review.
 *  (h) Single-row failures raise a shell toast (states-07 item 2): a reject
 *      500 is a `.toast[role=alert]` and no `.table-error`; an approve
 *      failure raised while the review dialog is open is visible and
 *      announced inside it.
 *  (i) The chevron turns on the expanded row, and not under reduced motion.
 *  (j) axe on the expanded row with the banner and on the outcome confirm.
 *
 * Bulk Reject (tables-07, proof (c)) is this lane's budget cut 3: not built.
 * Holds are RequestGates or held routes, never wall-clock waits.
 */
import type { Locator, Page } from '@playwright/test';
import type { ApproveResult, DecisionReceipt } from '../../../src/lib/apiTypes';
import type { LeadSummary, SessionResponse } from '../../../src/types';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { APPROVE_AUDIT_ID, approveResult, ledgerReceipt } from './data/decisionReceipt';
import {
  ACTIONED_LEAD,
  registerAssignmentOutcome,
  registerLeadQueue,
  registerRejectRecorder,
  registerWideFootprint,
} from './data/leadQueue';
import { VIRTUAL_QUEUE, registerVirtualQueue } from './data/queueKeyboard';
import { DNC_LEAD, QUEUE_LAYOUT_LEADS, registerQueueLayoutLeads } from './data/queueLayout';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const ELIGIBLE = PRIMARY_BORROWER.borrower_id;
const TERMINAL = LEADS[1].borrower_id;
const BANNER_COPY = `Approve ${ELIGIBLE} for outreach? Nothing is sent until you approve.`;

function calls(mockApi: MockApi, method: string, path: string): number {
  return mockApi.calls.filter((call) => call.method === method && call.path === path).length;
}

function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

function banner(page: Page, borrowerId: string): Locator {
  return page.getByTestId(`lead-row-approval-${borrowerId}`);
}

async function expand(page: Page, borrowerId: string): Promise<void> {
  const toggle = page.getByRole('button', { name: `Toggle preview for lead ${borrowerId}` });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
}

/** The topmost element at the locator's centre is the element itself (or inside it). */
async function isTopmostAtCentre(target: Locator): Promise<boolean> {
  return target.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return hit !== null && (hit === el || el.contains(hit));
  });
}

interface BannerLayout {
  /** The copy column's share of the banner's width. */
  copyShare: number;
  /** The title's height in lines of its own line-height. */
  titleLines: number;
  /** The actions start below the copy column (their own line). */
  actionsUnderCopy: boolean;
}

async function bannerLayout(gate: Locator): Promise<BannerLayout> {
  return gate.locator('.approval').evaluate((approval) => {
    const box = (selector: string) => approval.querySelector(selector)?.getBoundingClientRect() ?? new DOMRect();
    const title = approval.querySelector('.approval__title');
    const style = title ? getComputedStyle(title) : null;
    const lineHeight = !style ? 0 : style.lineHeight === 'normal'
      ? Number.parseFloat(style.fontSize) * 1.2
      : Number.parseFloat(style.lineHeight);
    const body = box('.approval__body');
    return {
      copyShare: body.width / approval.getBoundingClientRect().width,
      titleLines: lineHeight > 0 ? box('.approval__title').height / lineHeight : Number.POSITIVE_INFINITY,
      actionsUnderCopy: box('.approval__actions').top >= body.bottom - 1,
    };
  });
}

/**
 * Linux Chromium sets Geist wider than macOS (segments-cards'
 * LINUX_TEXT_EMULATION): off Linux the title is widened by about that much,
 * so its one-line fit holds on the CI renderer too.
 */
const LINUX_TITLE_EMULATION = '.approval__title { letter-spacing: 0.5px; }';

function registerApproveAndReceipt(mockApi: MockApi): void {
  mockApi.register<ApproveResult>('POST', '/api/outreach/approve', () => approveResult(APPROVE_AUDIT_ID));
  mockApi.register<DecisionReceipt>('GET', '/api/audit/receipt/:id', ({ params }) =>
    json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
  );
}

test.describe('(a) the expanded row\'s approval banner', () => {
  test('shows with the exact copy; expanding reads and drafts nothing; its Approve opens the review with one draft', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/lead-queue');
    const naturalLoad = markNaturalLoad(mockApi);

    await expand(page, ELIGIBLE);
    const gate = banner(page, ELIGIBLE);
    await expect(gate.locator('.approval__sub').first()).toHaveText(BANNER_COPY);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'expanding the row');
    expect(calls(mockApi, 'POST', '/api/outreach/draft'), 'expanding drafts nothing').toBe(0);
    expect(calls(mockApi, 'POST', '/api/outreach/approve')).toBe(0);

    await gate.getByRole('button', { name: 'Approve outreach' }).click();
    const review = page.getByTestId('lead-approve-review');
    await expect(review.getByTestId('lead-approve-review-subject')).not.toBeEmpty();
    await expect(page.locator('tr.tbl__expand').getByTestId('lead-approve-review'), 'the review opened inline').toBeVisible();
    await expect(gate, 'two approval gates never stack').toHaveCount(0);
    expect(calls(mockApi, 'POST', '/api/outreach/draft'), 'exactly the review\'s one draft').toBe(1);
    expect(calls(mockApi, 'POST', '/api/outreach/approve')).toBe(0);
  });

  test('is gone after the decision\'s receipt, and absent on terminal and non-actionable rows', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    registerApproveAndReceipt(mockApi);
    await app.gotoRoute('/lead-queue');

    await expand(page, TERMINAL);
    await expect(page.locator('tr.tbl__expand')).toBeVisible();
    await expect(banner(page, TERMINAL), 'a terminal row').toHaveCount(0);
    await expand(page, DNC_LEAD.borrower_id);
    await expect(banner(page, DNC_LEAD.borrower_id), 'a non-actionable (DNC) row').toHaveCount(0);

    await expand(page, ELIGIBLE);
    await banner(page, ELIGIBLE).getByRole('button', { name: 'Approve outreach' }).click();
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(page.getByTestId(`lead-approval-cell-${ELIGIBLE}`).locator('.chip--success')).toBeVisible();
    await expect(page.getByTestId('decision-receipt')).toBeVisible();
    await expect(banner(page, ELIGIBLE), 'no banner after the decision').toHaveCount(0);
  });

  test('a gated approver sees it, disabled, with the reason', async ({ app, mockApi, page }) => {
    mockApi.register<SessionResponse>('GET', '/api/session', () => ({
      body: { can_access_admin: true, can_approve: false, actor_email: 'analyst@summit-mortgage.example' },
    }));
    await app.gotoRoute('/lead-queue');
    await expand(page, ELIGIBLE);
    const gate = banner(page, ELIGIBLE);
    await expect(gate).toBeVisible();
    await expect(gate.getByRole('button', { name: 'Approve outreach' })).toBeDisabled();
    await expect(gate.getByRole('button', { name: 'Reject' })).toBeDisabled();
    await expect(gate.getByTestId('approval-gate-reason')).toContainText('Requires approver role');
  });

  test('its Approve is the topmost element at its centre, Console closed and open', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await expand(page, ELIGIBLE);
    const approve = banner(page, ELIGIBLE).getByRole('button', { name: 'Approve outreach' });
    await approve.scrollIntoViewIfNeeded();
    await expect(approve).toBeInViewport({ ratio: 1 });
    expect(await isTopmostAtCentre(approve), 'Console closed').toBe(true);

    await app.openConsole();
    await approve.scrollIntoViewIfNeeded();
    await expect(approve).toBeInViewport({ ratio: 1 });
    expect(await isTopmostAtCentre(approve), 'Console open').toBe(true);
  });

  // The preview's third column is ~390px (Console closed) to ~490px (Console
  // open): the copy keeps its width beside the icon, with the title on one
  // line, and the actions wrap under it (the declared departure in
  // LeadTable.css), instead of the copy shrinking to a word per line.
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the copy keeps its width and the actions wrap under it, Console closed and open`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TITLE_EMULATION });
      await expand(page, ELIGIBLE);
      const gate = banner(page, ELIGIBLE);
      await expect(gate.locator('.approval')).toBeVisible();
      for (const consoleState of ['closed', 'open'] as const) {
        if (consoleState === 'open') await app.openConsole();
        const measure = () => bannerLayout(gate);
        await expect.poll(async () => (await measure()).copyShare, { message: `Console ${consoleState}: the copy keeps half the banner` })
          .toBeGreaterThanOrEqual(0.5);
        await expect.poll(async () => (await measure()).titleLines, { message: `Console ${consoleState}: the title fits one line` })
          .toBeLessThanOrEqual(1.5);
        expect((await measure()).actionsUnderCopy, `Console ${consoleState}: the actions wrap under the copy`).toBe(true);
      }
    });
  }
});

interface FocusStop {
  label: string;
  covered: string[];
}

/** The focused control, and which sticky chrome (if any) fully covers its box. */
async function focusStop(page: Page): Promise<FocusStop | null> {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!(el instanceof HTMLElement) || el === document.body) return null;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return null;
    const within = (area: { left: number; right: number; top: number; bottom: number }) => (
      box.left >= area.left - 1 && box.right <= area.right + 1 && box.top >= area.top - 1 && box.bottom <= area.bottom + 1
    );
    const covered: string[] = [];
    const wrap = document.querySelector<HTMLElement>('.tbl-wrap');
    // The header CELLS are sticky (not the <thead>, which scrolls away):
    // their band across the scrollport is what covers a row.
    const headCell = wrap?.querySelector('thead th');
    if (wrap && headCell && !wrap.querySelector('thead')?.contains(el)) {
      const band = headCell.getBoundingClientRect();
      const port = wrap.getBoundingClientRect();
      if (within({ left: port.left, right: port.right, top: band.top, bottom: band.bottom })) covered.push('thead');
    }
    const pin = wrap?.querySelector('.lead-table__approval-header');
    if (wrap && pin && wrap.scrollWidth > wrap.clientWidth && !el.closest('.tbl-cell--approval, .lead-table__approval-header')) {
      const column = pin.getBoundingClientRect();
      const port = wrap.getBoundingClientRect();
      if (within({ left: column.left, right: column.right, top: port.top, bottom: port.bottom })) covered.push('pinned column');
    }
    for (const [selector, name] of [['.bulk-actions', 'bulk bar'], ['.route-nav', 'route nav']] as const) {
      const bar = document.querySelector(selector);
      if (!bar || bar.contains(el) || getComputedStyle(bar).position !== 'sticky') continue;
      if (within(bar.getBoundingClientRect())) covered.push(name);
    }
    const label = el.getAttribute('aria-label') ?? el.getAttribute('data-testid') ?? el.textContent?.trim().slice(0, 40) ?? el.tagName;
    return { label, covered };
  });
}

/** Press a key `presses` times, recording each focus stop. */
async function walk(page: Page, key: 'Tab' | 'Shift+Tab', presses: number): Promise<FocusStop[]> {
  const stops: FocusStop[] = [];
  for (let index = 0; index < presses; index += 1) {
    await page.keyboard.press(key);
    const stop = await focusStop(page);
    if (stop) stops.push(stop);
  }
  return stops;
}

/**
 * Scroll the table so row `index` sits exactly behind the sticky header,
 * inside the scrollport. A control there is the worst case: the browser
 * treats it as already in view, so without the table's clearance a focus leaves it
 * hidden under the header.
 */
async function rowBehindHeader(page: Page, index: number): Promise<string> {
  const placed = await tableWrap(page).evaluate((wrap, rowIndex) => {
    const row = wrap.querySelectorAll<HTMLElement>('tr[data-borrower-row]')[rowIndex];
    wrap.scrollTop += row.getBoundingClientRect().top - wrap.getBoundingClientRect().top;
    const offset = row.getBoundingClientRect().top - wrap.getBoundingClientRect().top;
    return { id: row.dataset.borrowerRow ?? '', offset };
  }, index);
  expect(placed.id, 'the row exists').not.toBe('');
  expect(Math.abs(placed.offset), 'precondition: the row starts at the scrollport top, behind the header').toBeLessThanOrEqual(1);
  return placed.id;
}

/** How far below `.main`'s scrollport top the walks below start. */
const WALK_START_DEPTH = 250;

/**
 * `.main` at its natural end (no spacer) and the table scroller at its
 * middle, so the rows run up under the sticky route nav, where the browser
 * counts a control as in view. Returns the row checkbox a walk up starts
 * from, exactly WALK_START_DEPTH below `.main`'s scrollport top (the table is
 * nudged less than a row to put one there, so every run is the same). Where
 * the table ends above that depth (/segment-intelligence), `.main` backs off
 * just enough to show it.
 */
async function tableUpUnderRouteNav(page: Page): Promise<Locator> {
  const placed = await page.locator('.main').evaluate((main, depth) => {
    const nav = main.querySelector<HTMLElement>('.route-nav');
    const wrap = main.querySelector<HTMLElement>('.tbl-wrap');
    if (!nav || !wrap || getComputedStyle(nav).position !== 'sticky') throw new Error('no sticky route nav or no table');
    const boxes = [...wrap.querySelectorAll<HTMLElement>('tr[data-borrower-row] [data-testid^="lead-select-"]')];
    const boxHeight = boxes[0]?.getBoundingClientRect().height ?? 0;
    main.scrollTop = main.scrollHeight - main.clientHeight;
    const target = main.getBoundingClientRect().top + depth;
    const shortfall = target + boxHeight + 4 - wrap.getBoundingClientRect().bottom;
    if (shortfall > 0) main.scrollTop -= shortfall;
    wrap.scrollTop = Math.round((wrap.scrollHeight - wrap.clientHeight) / 2);
    const below = boxes.find((box) => box.getBoundingClientRect().top >= target);
    if (below) wrap.scrollTop += below.getBoundingClientRect().top - target;
    const view = main.getBoundingClientRect();
    const port = wrap.getBoundingClientRect();
    const start = below?.getBoundingClientRect();
    return {
      mainAtEnd: main.scrollHeight - main.clientHeight - main.scrollTop,
      mainScrollTop: main.scrollTop,
      tableScrollTop: wrap.scrollTop,
      tableTopUnderNav: port.top < nav.getBoundingClientRect().bottom,
      startOffset: start ? start.top - (view.top + depth) : Number.NaN,
      startInView: start ? start.bottom <= Math.min(port.bottom, view.bottom) : false,
      start: below?.dataset.testid ?? '',
    };
  }, WALK_START_DEPTH);
  expect(placed.mainScrollTop, `precondition: \`.main\` scrolls by itself at 1440x900 (${placed.mainAtEnd}px short of its end)`).toBeGreaterThan(0);
  expect(placed.tableScrollTop, 'precondition: the table scroller sits part-way down').toBeGreaterThan(0);
  expect(placed.tableTopUnderNav, 'precondition: the table\'s top edge is up under (or past) the route nav').toBe(true);
  expect(placed.start, `precondition: a row checkbox ${WALK_START_DEPTH}px below the scrollport top`).not.toBe('');
  expect(Math.abs(placed.startOffset), 'precondition: the start sits at that depth').toBeLessThanOrEqual(1);
  expect(placed.startInView, 'precondition: the start is in full view').toBe(true);
  return page.getByTestId(placed.start);
}

/** Scroll the table sideways so `target` sits fully under the pinned Approval column. */
async function underPinnedColumn(page: Page, target: Locator): Promise<void> {
  const inside = await target.evaluate((element) => {
    const wrap = element.closest('.tbl-wrap');
    const pin = wrap?.querySelector('.lead-table__approval-header');
    if (!wrap || !pin) throw new Error('no pinned Approval column');
    wrap.scrollLeft += element.getBoundingClientRect().right - (pin.getBoundingClientRect().right - 4);
    const box = element.getBoundingClientRect();
    const column = pin.getBoundingClientRect();
    return box.left >= column.left && box.right <= column.right;
  });
  expect(inside, 'precondition: the control sits under the pinned column').toBe(true);
}

/** LeadTable.css's clearance of the route nav (and the thead) on the table's focus targets. */
const ZERO_TABLE_BLOCK_MARGIN = '.lead-table__table tr, .lead-table__table tr * { scroll-margin-block-start: 0px !important; }';
/** Both of LeadTable.css's clearances: that margin, and the scroller's inline-end padding for the pin. */
const ZERO_TABLE_CLEARANCE = `${ZERO_TABLE_BLOCK_MARGIN} .tbl-wrap { scroll-padding: 0px !important; }`;
/** 38-focus-clearance.css's mechanism: the nav's size as scroll-margin on what is outside it. */
const ZERO_NAV_MARGIN = '.main * { scroll-margin-block-start: 0px !important; }';
/** --focus-ring-width + --focus-ring-offset (tokens.css). */
const FOCUS_RING_ROOM = 4;

/** A tall block at the end of `.main`, so any control in it can scroll to the top. */
async function addScrollRoom(page: Page): Promise<void> {
  await page.locator('.main').evaluate((main) => {
    const spacer = document.createElement('div');
    spacer.style.blockSize = '3000px';
    main.append(spacer);
  });
}

/**
 * Scroll `.main` so `target` sits entirely behind the sticky route nav. The
 * browser counts a control there as in view, so without a clearance a focus
 * leaves it hidden under the nav.
 */
async function behindRouteNav(page: Page, target: Locator): Promise<void> {
  await addScrollRoom(page);
  const covered = await target.evaluate((element) => {
    const main = element.closest<HTMLElement>('.main');
    const nav = main?.querySelector<HTMLElement>('.route-nav');
    if (!main || !nav || getComputedStyle(nav).position !== 'sticky') throw new Error('no sticky route nav');
    main.scrollTop += element.getBoundingClientRect().top - (nav.getBoundingClientRect().top + 2);
    const box = element.getBoundingClientRect();
    const band = nav.getBoundingClientRect();
    return box.top >= band.top && box.bottom <= band.bottom;
  });
  expect(covered, 'precondition: the control sits behind the route nav').toBe(true);
}

/** The control's top edge below the route nav's bottom edge (negative: under it). */
async function gapBelowRouteNav(target: Locator): Promise<number> {
  return target.evaluate((element) => (
    element.getBoundingClientRect().top - (document.querySelector('.route-nav')?.getBoundingClientRect().bottom ?? 0)
  ));
}

/** Zeroes the pinned controls' own scroll-margin (LeadTable.css), leaving the scroller's padding. */
const ZERO_PIN_MARGIN = '.lead-table__table .tbl-cell--approval * { scroll-margin-inline: 0px !important; }';
/** 38-focus-clearance.css's nav margin, put back on the in-place listbox's options. */
const NAV_MARGIN_ON_MENU = '.filter-menu__item { scroll-margin-block-start: 61px !important; }';

async function scrollLeftOf(page: Page): Promise<number> {
  return tableWrap(page).evaluate((wrap) => wrap.scrollLeft);
}

/**
 * Focus each pinned control of `borrowerId`'s row with the table scrolled to
 * `start`, and return where scrollLeft ended up after each focus.
 */
async function scrollLeftAfterPinnedFocus(page: Page, borrowerId: string, start: number): Promise<number[]> {
  const ends: number[] = [];
  for (const control of [page.getByTestId(`lead-approve-${borrowerId}`), page.getByTestId(`lead-reject-${borrowerId}`)]) {
    await tableWrap(page).evaluate((wrap, left) => { wrap.scrollLeft = left; }, start);
    expect(Math.abs((await scrollLeftOf(page)) - start), 'precondition: the table sits at its start offset').toBeLessThanOrEqual(1);
    await control.focus();
    await expect(control).toBeFocused();
    ends.push(await scrollLeftOf(page));
  }
  return ends;
}

/** Two animation frames: a React effect run by the last event has painted. */
async function settleFrames(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
}

/**
 * Scroll the open STATE menu (the pointer outside it), move the pointer to
 * the centre of the top option it shows in full, let the hover settle, then
 * press and release there. Returns the option aimed at and the `state`
 * param the pick wrote (null: 'All states').
 */
async function pointerPickTopVisibleState(page: Page, menu: Locator): Promise<{ aimed: string; written: string | null }> {
  const aim = await menu.evaluate((list) => {
    list.scrollTop = 150;
    const top = list.getBoundingClientRect().top + list.clientTop;
    const option = [...list.querySelectorAll<HTMLElement>('[role="option"]')]
      .find((candidate) => candidate.getBoundingClientRect().top >= top - 0.5);
    if (!option) throw new Error('no option in view');
    const box = option.getBoundingClientRect();
    return {
      // The label only: an opened menu may add an aria-hidden option count (W5a).
      text: option.firstChild?.textContent?.trim() ?? '',
      x: box.left + box.width / 2,
      y: box.top + box.height / 2,
      offset: box.top - top,
      scrollTop: list.scrollTop,
    };
  });
  expect(aim.scrollTop, 'precondition: the menu scrolled').toBe(150);
  expect(aim.offset, 'precondition: the aimed option sits inside the nav margin\'s reach').toBeLessThan(30);
  await page.mouse.move(aim.x, aim.y);
  await expect(menu.locator('.filter-menu__item.is-focused'), 'the hover made an option active').toHaveCount(1);
  await settleFrames(page);
  await settleFrames(page);
  await page.mouse.down();
  await page.mouse.up();
  await expect(menu).toHaveCount(0);
  return { aimed: aim.text, written: new URL(page.url()).searchParams.get('state') };
}

test.describe('(b) keyboard focus is never hidden under sticky chrome', () => {
  for (const route of ['/lead-queue', '/segment-intelligence'] as const) {
    test(`${route}: with .main at its end, Shift+Tab up the rows stops clear of the sticky route nav`, async ({ app, page }) => {
      await app.gotoRoute(route);
      await (await tableUpUnderRouteNav(page)).focus();
      const stops = await walk(page, 'Shift+Tab', 30);
      expect(stops.length, 'non-vacuity: the walk moved focus').toBeGreaterThan(20);
      expect(stops.filter((stop) => stop.covered.length > 0)).toEqual([]);
    });

    test(`${route} non-vacuity: with the table's block margin zeroed, the same walk stops under the route nav`, async ({ app, page }) => {
      await app.gotoRoute(route);
      await page.addStyleTag({ content: ZERO_TABLE_BLOCK_MARGIN });
      await (await tableUpUnderRouteNav(page)).focus();
      const stops = await walk(page, 'Shift+Tab', 30);
      expect(stops.filter((stop) => stop.covered.includes('route nav')).length, 'a stop under the nav').toBeGreaterThan(0);
    });
  }

  /** K from the walk's start, `presses` times: the cursor row's gap below the nav after each move. */
  async function cursorWalkUp(page: Page, presses: number): Promise<Array<{ id: string | null; gap: number }>> {
    await (await tableUpUnderRouteNav(page)).focus();
    const cursor = page.locator('table.tbl tr.is-cursor');
    const gaps: Array<{ id: string | null; gap: number }> = [];
    let previous = await cursor.getAttribute('data-borrower-row');
    expect(previous, 'precondition: the focus put the cursor on the start row').toBeTruthy();
    for (let index = 0; index < presses; index += 1) {
      await page.keyboard.press('k');
      await expect(cursor, 'K moved the cursor').not.toHaveAttribute('data-borrower-row', previous ?? '');
      await settleFrames(page);
      previous = await cursor.getAttribute('data-borrower-row');
      gaps.push({ id: previous, gap: await gapBelowRouteNav(cursor) });
    }
    return gaps;
  }

  test('/lead-queue: with .main at its end, K brings the cursor row up clear of the sticky route nav', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const gaps = await cursorWalkUp(page, 8);
    expect(gaps.filter((stop) => stop.gap < -0.5)).toEqual([]);
  });

  test('/lead-queue non-vacuity: with the table\'s block margin zeroed, K leaves a cursor row under the route nav', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await page.addStyleTag({ content: ZERO_TABLE_BLOCK_MARGIN });
    const gaps = await cursorWalkUp(page, 8);
    expect(gaps.filter((stop) => stop.gap < -0.5).length, 'a cursor row under the nav').toBeGreaterThan(0);
  });

  test('Shift+Tab up the rows stops clear of the sticky header, with a selection bar up', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await page.getByTestId(`lead-select-${LEADS[0].borrower_id}`).check();
    await page.getByTestId(`lead-select-${LEADS[2].borrower_id}`).check();
    await expect(page.getByTestId('lead-bulk-actions')).toBeVisible();
    const hidden = await rowBehindHeader(page, 5);
    await page.getByTestId(`lead-select-${LEADS[6].borrower_id}`).focus();

    const stops = await walk(page, 'Shift+Tab', 60);
    expect(stops.length, 'non-vacuity: the walk moved focus').toBeGreaterThan(40);
    expect(stops.filter((stop) => stop.covered.length > 0)).toEqual([]);

    await rowBehindHeader(page, 5);
    await page.getByTestId(`lead-select-${hidden}`).focus();
    expect((await focusStop(page))?.covered, 'a control behind the header scrolls clear of it').toEqual([]);
  });

  test('Tab across a row reaches its Approval cell with the Console open, never under the pinned column', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    const overflow = await tableWrap(page).evaluate((wrap) => wrap.scrollWidth - wrap.clientWidth);
    expect(overflow, 'precondition: the Console narrows the table into a horizontal scroll').toBeGreaterThan(0);
    await page.getByTestId(`lead-select-${ELIGIBLE}`).focus();

    const stops = await walk(page, 'Tab', 10);
    expect(stops.some((stop) => stop.label === `Approve ${ELIGIBLE}`), 'the walk reached the Approval cell').toBe(true);
    expect(stops.filter((stop) => stop.covered.length > 0)).toEqual([]);

    // The Status cell's `+n`, the last control before the pin, parked under it.
    const more = page.locator(`[data-testid="lead-status-${DNC_LEAD.borrower_id}"] .lead-table__more`);
    await underPinnedColumn(page, more);
    await more.focus();
    expect((await focusStop(page))?.covered, 'a control under the pin scrolls clear of it').toEqual([]);
  });

  test('non-vacuity: with the table\'s clearances zeroed, the same stops are covered', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await page.addStyleTag({ content: ZERO_TABLE_CLEARANCE });
    // A focus moved to a control behind the header (the flow's focus hand-offs,
    // J / K's scrollIntoView) is left there: the browser counts it as in view.
    const hidden = await rowBehindHeader(page, 5);
    await page.getByTestId(`lead-select-${hidden}`).focus();
    expect((await focusStop(page))?.covered, 'a stop under the header').toContain('thead');

    await app.openConsole();
    const more = page.locator(`[data-testid="lead-status-${DNC_LEAD.borrower_id}"] .lead-table__more`);
    await underPinnedColumn(page, more);
    await more.focus();
    expect((await focusStop(page))?.covered, 'a stop under the pin').toContain('pinned column');
  });

  test('a control focused from behind the sticky route nav stops below it (/glossary)', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    const target = page.locator('.main .proto-hero').getByRole('link', { name: 'Back to leads' });
    await behindRouteNav(page, target);
    await target.focus();
    expect(await gapBelowRouteNav(target), 'the focused control sits below the nav').toBeGreaterThanOrEqual(-0.5);
  });

  test('non-vacuity: with the route-nav scroll-margin zeroed, that control stays behind the nav', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    await page.addStyleTag({ content: ZERO_NAV_MARGIN });
    const target = page.locator('.main .proto-hero').getByRole('link', { name: 'Back to leads' });
    await behindRouteNav(page, target);
    await target.focus();
    expect(await gapBelowRouteNav(target), 'a stop under the nav').toBeLessThan(0);
  });

  for (const zeroed of [false, true]) {
    test(`${zeroed ? 'non-vacuity, block margin zeroed: ' : ''}the expanded row's Approve focused from behind the route nav ${zeroed ? 'stays there' : 'stops below it'}`, async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      if (zeroed) await page.addStyleTag({ content: ZERO_TABLE_BLOCK_MARGIN });
      await expand(page, ELIGIBLE);
      const approve = banner(page, ELIGIBLE).getByRole('button', { name: 'Approve outreach' });
      await behindRouteNav(page, approve);
      await approve.focus();
      if (zeroed) expect(await gapBelowRouteNav(approve), 'a stop under the nav').toBeLessThan(0);
      else expect(await gapBelowRouteNav(approve), 'the focused control sits below the nav').toBeGreaterThanOrEqual(-0.5);
    });
  }

  test('/ask-genie clears the nav once: its own .main padding, with no second margin on top', async ({ app, page }) => {
    await app.gotoRoute('/ask-genie');
    const panel = page.locator('section[role="tabpanel"]:not([hidden])');
    await expect(panel.locator('.genie-composer'), 'precondition: the Ask tab (ask-genie.css) is showing').toBeVisible();
    await addScrollRoom(page);
    const target = panel.locator('button:not(.genie-composer *)').first();
    const gap = await target.evaluate((element) => {
      element.scrollIntoView({ block: 'start' });
      return element.getBoundingClientRect().top - (document.querySelector('.route-nav')?.getBoundingClientRect().bottom ?? 0);
    });
    // ask-genie.css pads `.main` by the measured nav plus the focus ring
    // (2px + 2px); 38-focus-clearance's margin would put the nav in twice.
    expect(gap, 'at or below the nav').toBeGreaterThanOrEqual(-0.5);
    expect(gap, 'the focus ring\'s room, not a second nav height').toBeLessThanOrEqual(FOCUS_RING_ROOM + 1);
  });

  test('/ask-genie\'s Workflows tab keeps the route-nav clearance: only the Ask tab is left out', async ({ app, page }) => {
    await app.gotoRoute('/ask-genie?tab=workflows');
    const panel = page.locator('section[role="tabpanel"]:not([hidden])');
    await expect(panel.getByRole('button').first(), 'precondition: the Workflows tab shows').toBeVisible();
    await expect(panel.locator('.genie-composer')).toHaveCount(0);
    await addScrollRoom(page);
    const gap = await panel.getByRole('button').first().evaluate((element) => {
      element.scrollIntoView({ block: 'start' });
      return element.getBoundingClientRect().top - (document.querySelector('.route-nav')?.getBoundingClientRect().bottom ?? 0);
    });
    expect(gap, 'at or below the nav').toBeGreaterThanOrEqual(-0.5);
    expect(gap, 'one clearance').toBeLessThanOrEqual(FOCUS_RING_ROOM + 1);
  });

  test('a focus on a pinned Approve or Reject leaves the table\'s scrollLeft where it was, Console open', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    const overflow = await tableWrap(page).evaluate((wrap) => wrap.scrollWidth - wrap.clientWidth);
    expect(overflow, 'precondition: the Console narrows the table into a horizontal scroll').toBeGreaterThan(40);

    const middle = Math.round(overflow / 2);
    for (const start of [0, middle]) {
      for (const end of await scrollLeftAfterPinnedFocus(page, ELIGIBLE, start)) {
        expect(Math.abs(end - start), `a pinned control focused at scrollLeft ${start} stays there`).toBeLessThanOrEqual(1);
      }
    }

    // Shift+Tab from the next row's checkbox lands on this row's Reject, in the pin.
    await tableWrap(page).evaluate((wrap) => { wrap.scrollLeft = 0; });
    const order = await page.locator('tr[data-borrower-row]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-borrower-row')));
    const next = order[order.indexOf(ELIGIBLE) + 1];
    expect(next, 'precondition: a row follows').toBeTruthy();
    await page.getByTestId(`lead-select-${next}`).focus();
    await page.keyboard.press('Shift+Tab');
    await expect(page.getByTestId(`lead-reject-${ELIGIBLE}`)).toBeFocused();
    expect(await scrollLeftOf(page), 'the row\'s borrower id stays in view').toBeLessThanOrEqual(1);
    const idInView = await page.getByRole('button', { name: `Toggle preview for lead ${ELIGIBLE}` }).evaluate((id) => {
      const port = id.closest('.tbl-wrap')?.getBoundingClientRect();
      return port ? id.getBoundingClientRect().left >= port.left - 1 : false;
    });
    expect(idInView, 'the row\'s borrower id is still inside the scrollport').toBe(true);
  });

  test('non-vacuity: with the pinned controls\' margin zeroed, a focus on one scrolls the table to its end, and the guard puts it back', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    await page.addStyleTag({ content: ZERO_PIN_MARGIN });
    const overflow = await tableWrap(page).evaluate((wrap) => wrap.scrollWidth - wrap.clientWidth);
    expect(overflow).toBeGreaterThan(40);
    for (const testId of [`lead-approve-${ELIGIBLE}`, `lead-reject-${ELIGIBLE}`]) {
      // One evaluate: the synchronous read sees the engine's scroll before
      // the pinned-focus guard's microtask (useTableScrollClearance) runs.
      const offsets = await page.getByTestId(testId).evaluate(async (control) => {
        const wrap = control.closest<HTMLElement>('.tbl-wrap');
        if (!wrap) throw new Error('the control is not in the table scroller');
        wrap.scrollLeft = 0;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        (control as HTMLElement).focus();
        const synchronous = wrap.scrollLeft;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return { synchronous, settled: wrap.scrollLeft };
      });
      expect(offsets.synchronous, 'the scroller\'s inline-end padding "reveals" a control already in view').toBeGreaterThan(overflow - 2);
      expect(offsets.settled, 'the pinned-focus guard restored the offset').toBeLessThanOrEqual(1);
    }
  });

  test('a pointer pick of the top visible option in a scrolled STATE menu writes that option', async ({ app, mockApi, page }) => {
    registerWideFootprint(mockApi);
    await app.gotoRoute('/lead-queue');
    const menu = await app.openFilterMenu('STATE');
    await expect(menu.getByRole('option')).toHaveCount(25);
    const pick = await pointerPickTopVisibleState(page, menu);
    expect(pick.written, `aimed at ${pick.aimed}`).toBe(pick.aimed);
  });

  test('non-vacuity: with the nav\'s margin on the menu\'s options, the same pick writes another state', async ({ app, mockApi, page }) => {
    registerWideFootprint(mockApi);
    await app.gotoRoute('/lead-queue');
    await page.addStyleTag({ content: NAV_MARGIN_ON_MENU });
    const menu = await app.openFilterMenu('STATE');
    await expect(menu.getByRole('option')).toHaveCount(25);
    const pick = await pointerPickTopVisibleState(page, menu);
    expect(pick.written, `aimed at ${pick.aimed}: the hover rolled the menu back under the pointer`).not.toBe(pick.aimed);
  });
});

test.describe('(d) the assignment outcome needs a confirm', () => {
  function outcomeQueue(): LeadSummary[] {
    return LEADS.map((lead, index) => (index === 4 ? ACTIONED_LEAD : lead));
  }
  const id = ACTIONED_LEAD.borrower_id;

  test('Escape and Cancel make no POST and hand focus back; Record posts once after its confirm row', async ({ app, mockApi, page }) => {
    registerLeadQueue(mockApi, outcomeQueue());
    const recorder = registerAssignmentOutcome(mockApi);
    await app.gotoRoute('/lead-queue');
    await expand(page, id);
    const control = page.getByTestId(`lifecycle-advance-${id}`);
    const opener = control.getByRole('button', { name: `Record outcome for ${id}` });

    await opener.click();
    await control.getByRole('button', { name: 'Success' }).click();
    await expect(page.getByTestId(`lifecycle-outcome-confirm-${id}`)).toContainText(`Record Success for ${id}?`);
    await page.keyboard.press('Escape');
    await expect(opener).toBeFocused();
    expect(recorder.bodies, 'Escape wrote nothing').toHaveLength(0);

    await opener.click();
    await control.getByRole('button', { name: 'Cancel' }).click();
    await expect(opener).toBeFocused();
    expect(recorder.bodies, 'Cancel wrote nothing').toHaveLength(0);

    await opener.click();
    await control.getByRole('button', { name: 'Declined' }).click();
    expect(recorder.bodies, 'picking an outcome writes nothing').toHaveLength(0);
    await control.getByRole('button', { name: 'Record', exact: true }).click();
    await expect.poll(() => recorder.bodies.length).toBe(1);
    expect(recorder.bodies[0].outcome).toBe('declined');
  });

  test('a 409 keeps the stage and shows the error', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 409/);
    registerLeadQueue(mockApi, outcomeQueue());
    const recorder = registerAssignmentOutcome(mockApi, { conflict: true });
    await app.gotoRoute('/lead-queue');
    await expand(page, id);
    const control = page.getByTestId(`lifecycle-advance-${id}`);
    await control.getByRole('button', { name: `Record outcome for ${id}` }).click();
    await control.getByRole('button', { name: 'No response' }).click();
    await control.getByRole('button', { name: 'Record', exact: true }).click();

    await expect(control.locator('[role="alert"]')).toContainText('no longer actioned');
    expect(recorder.bodies).toHaveLength(1);
    await expect(page.getByTestId(`lead-workflow-${id}`).locator('.chip__label', { hasText: /^Actioned$/ })).toBeVisible();
  });
});

test.describe('(e) compliance sources are visible text', () => {
  test('a DNC row names its DNC source; an eligible row its eligibility source', async ({ app, mockApi, page }) => {
    registerQueueLayoutLeads(mockApi, () => QUEUE_LAYOUT_LEADS);
    await app.gotoRoute('/lead-queue');
    await expand(page, DNC_LEAD.borrower_id);
    await expect(page.getByTestId(`lead-eligibility-source-${DNC_LEAD.borrower_id}`)).toHaveText('DNC source: synthetic_seed');
    await expect(page.getByTestId(`lead-eligibility-source-${DNC_LEAD.borrower_id}`)).toBeVisible();
    await expand(page, ELIGIBLE);
    await expect(page.getByTestId(`lead-eligibility-source-${ELIGIBLE}`)).toHaveText('Eligibility source: synthetic_seed');
  });
});

/** The row's box sits inside the table scrollport, below the sticky header. */
async function expectRowInScrollport(page: Page, borrowerId: string): Promise<void> {
  await expect.poll(async () => tableWrap(page).evaluate((wrap, id) => {
    const row = wrap.querySelector(`tr[data-borrower-row="${id}"]`);
    if (!row) return 'not rendered';
    const port = wrap.getBoundingClientRect();
    const head = wrap.querySelector('thead')?.getBoundingClientRect().bottom ?? port.top;
    const box = row.getBoundingClientRect();
    return box.top >= head - 1 && box.bottom <= port.bottom + 1 ? 'inside' : `outside (${Math.round(box.top)})`;
  }, borrowerId), { message: `row ${borrowerId} inside the scrollport` }).toBe('inside');
}

test.describe('(f) a restored ?row= is revealed', () => {
  const row140 = VIRTUAL_QUEUE[140].borrower_id;

  test('a load and a PUSH naming row 140 of the virtualized queue put it inside the scrollport', async ({ app, mockApi, page }) => {
    registerVirtualQueue(mockApi);
    await app.gotoRoute(`/lead-queue?row=${row140}`);
    await expectRowInScrollport(page, row140);
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', row140);

    // A sort PUSHES an entry that keeps the row; the scroller starts at the top, then reveals it.
    const reads = calls(mockApi, 'GET', '/api/leads');
    await page.getByRole('button', { name: 'Sort by Equity' }).click();
    await expect(page).toHaveURL(new RegExp(`[?&]sort=equity&dir=desc.*row=${row140}|row=${row140}.*sort=equity`));
    await expectRowInScrollport(page, row140);
    expect(calls(mockApi, 'GET', '/api/leads'), 'the reveal reads nothing').toBe(reads);
  });
});

test.describe('(g) focus moves the cursor', () => {
  test('Tab to row B\'s checkbox with the cursor on A, then A: B\'s review opens', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await tableWrap(page).focus();
    await page.keyboard.press('j');
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', LEADS[0].borrower_id);

    const rowB = LEADS[2].borrower_id;
    await page.getByTestId(`lead-select-${rowB}`).focus();
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', rowB);
    await page.keyboard.press('a');
    const review = page.getByTestId('lead-approve-review');
    await expect(review).toContainText(rowB);
    await expect(review).not.toContainText(LEADS[0].borrower_id);
  });
});

test.describe('(h) single-row failures raise a shell toast', () => {
  test('a reject 500 is a .toast[role=alert] and never a .table-error in the table', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500/);
    const recorder = registerRejectRecorder(mockApi, { failIds: [ELIGIBLE] });
    await app.gotoRoute('/lead-queue');
    await page.getByTestId(`lead-reject-${ELIGIBLE}`).click();
    // No default reason (D-approval-flow-d item 13): the reviewer picks one.
    await page.getByTestId('lead-reject-reason').selectOption('low_intent');
    await page.getByRole('button', { name: 'Confirm reject' }).click();

    const toast = page.locator('.toast[role="alert"]');
    await expect(toast.locator('.toast__title')).toHaveText(`Couldn't reject ${ELIGIBLE}`);
    await expect(toast.locator('.toast__detail')).toHaveText('The server hit an unexpected error.');
    await expect(page.locator('.surface .table-error')).toHaveCount(0);
    expect(recorder.bodies).toHaveLength(1);
  });

  test('an approve failure raised while the review dialog is open is visible and announced', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500/);
    mockApi.register('POST', '/api/outreach/approve', () => json({ detail: 'Internal Server Error' }, { status: 500 }));
    await app.gotoRoute('/lead-queue');
    await page.getByTestId(`lead-approve-${ELIGIBLE}`).click();
    const dialog = page.locator('dialog[open]', { has: page.getByTestId('lead-approve-review') });
    const confirm = dialog.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeEnabled();
    await confirm.click();

    const toast = dialog.locator('.toast[role="alert"]');
    await expect(toast.locator('.toast__title')).toHaveText(`Couldn't approve ${ELIGIBLE}`);
    await expect(toast).toBeVisible();
    expect(await isTopmostAtCentre(toast), 'the toast is not under the modal').toBe(true);
  });
});

test.describe('(i) the expand chevron turns', () => {
  test('rotated 90deg on the expanded row; no transition under reduced motion', async ({ app, page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await app.gotoRoute('/lead-queue');
    await expand(page, ELIGIBLE);
    const chevron = page.locator(`tr.is-expanded[data-borrower-row="${ELIGIBLE}"] .lead-table__chevron`);
    await expect.poll(() => chevron.evaluate((icon) => getComputedStyle(icon).rotate)).toBe('90deg');
    expect(await chevron.evaluate((icon) => getComputedStyle(icon).transitionProperty)).toBe('rotate');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await chevron.evaluate((icon) => getComputedStyle(icon).transitionProperty)).toBe('none');
    const fade = page.locator('tr.tbl__expand .tbl__expand-inner--lead');
    expect(await fade.evaluate((inner) => getComputedStyle(inner).transitionProperty)).toBe('none');
  });
});

test.describe('(j) axe on the lane\'s new surfaces', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the expanded row with its banner, and the outcome confirm, are clean`, async ({ app, mockApi, page }) => {
      registerLeadQueue(mockApi, LEADS.map((lead, index) => (index === 4 ? ACTIONED_LEAD : lead)));
      registerAssignmentOutcome(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await expand(page, ELIGIBLE);
      await expect(banner(page, ELIGIBLE)).toBeVisible();
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'expanded-banner' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: 'tr.tbl__expand',
      });

      const id = ACTIONED_LEAD.borrower_id;
      await expand(page, id);
      const control = page.getByTestId(`lifecycle-advance-${id}`);
      await control.getByRole('button', { name: `Record outcome for ${id}` }).click();
      await control.getByRole('button', { name: 'Success' }).click();
      await expect(page.getByTestId(`lifecycle-outcome-confirm-${id}`)).toBeVisible();
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'outcome-confirm' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: 'tr.tbl__expand',
      });
    });
  }
});
