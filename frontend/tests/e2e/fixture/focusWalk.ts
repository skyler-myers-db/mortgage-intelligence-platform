/**
 * focus()-driven focus walks for the cross-engine fixture projects (manual
 * check 2026-09-30, a11y-v2 / WCAG 2.4.11 Focus Not Obscured).
 *
 * WebKit's Tab skips buttons, links and checkboxes on macOS and Linux
 * defaults (Safari's "Press Tab to highlight each item" / Full Keyboard
 * Access is off), so a keyboard Tab walk proves nothing there. These helpers
 * move focus the way sequential navigation does, with element.focus() on the
 * next or previous tabbable in DOM order (tabindex >= 0, not disabled, not
 * inert, rendered, visible), and after each move report which sticky chrome,
 * if any, fully covers the focused control's box. The focus scroll a browser
 * performs for element.focus() is the same "scroll into view if needed" a Tab
 * performs, so the clearance the walk measures is the one a keyboard user
 * gets.
 *
 * The setup helpers (the table up under the route nav, a row behind the
 * sticky header, a control under the pinned Approval column or behind the
 * route nav) mirror lead-queue.fixture.spec.ts's, which another lane owns.
 */
import type { Locator, Page } from '@playwright/test';
import { expect } from './test';

export type CoveringChrome = 'route nav' | 'thead' | 'pinned column' | 'bulk bar';

export interface FocusStop {
  label: string;
  covered: CoveringChrome[];
}

/** The focused control, and which sticky chrome (if any) fully covers its box. */
export async function focusStop(page: Page): Promise<FocusStop | null> {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!(el instanceof HTMLElement) || el === document.body) return null;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return null;
    const within = (area: { left: number; right: number; top: number; bottom: number }) => (
      box.left >= area.left - 1 && box.right <= area.right + 1 && box.top >= area.top - 1 && box.bottom <= area.bottom + 1
    );
    const covered: Array<'route nav' | 'thead' | 'pinned column' | 'bulk bar'> = [];
    const wrap = document.querySelector<HTMLElement>('.tbl-wrap');
    // The header CELLS are sticky (not the <thead>): their band across the
    // scrollport is what covers a row.
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

/** Two animation frames: a focus handler's effect has run and painted. */
export async function settleFrames(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
}

/**
 * Move focus to the next (or previous) tabbable after document.activeElement
 * inside `scope`, in DOM order, with element.focus(). Returns false at the end.
 */
export async function focusNext(page: Page, direction: 'forward' | 'reverse', scope = 'body'): Promise<boolean> {
  return page.evaluate(({ direction: dir, scope: scopeSelector }) => {
    const root = document.querySelector(scopeSelector);
    if (!root) throw new Error(`no focus-walk scope ${scopeSelector}`);
    const CANDIDATES = 'a[href], area[href], button, input, select, textarea, iframe, summary, [tabindex], [contenteditable=""], [contenteditable="true"]';
    const tabbable = (el: HTMLElement): boolean => {
      if (el.matches(':disabled') || el.closest('[inert]')) return false;
      if (el instanceof HTMLInputElement && el.type === 'hidden') return false;
      const index = el.getAttribute('tabindex');
      if (index !== null && Number(index) < 0) return false;
      const closed = el.closest('details:not([open])');
      if (closed && !(el.tagName === 'SUMMARY' && el.parentElement === closed)) return false;
      return el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
    };
    const list = [...root.querySelectorAll<HTMLElement>(CANDIDATES)].filter(tabbable);
    const active = document.activeElement;
    let at = active instanceof HTMLElement ? list.indexOf(active) : -1;
    if (at === -1 && active instanceof HTMLElement && active !== document.body) {
      // Focus sits on a non-tabbable (a programmatic target): step from its position.
      const after = list.findIndex((el) => (active.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0);
      at = dir === 'forward' ? (after === -1 ? list.length : after) - 1 : after === -1 ? list.length : after;
    }
    const next = list[dir === 'forward' ? at + 1 : at - 1];
    if (!next) return false;
    next.focus();
    return document.activeElement === next;
  }, { direction, scope });
}

/** `steps` focus() moves from the current focus, recording each stop after it settles. */
export async function focusWalk(page: Page, direction: 'forward' | 'reverse', steps: number, scope = 'body'): Promise<FocusStop[]> {
  const stops: FocusStop[] = [];
  for (let index = 0; index < steps; index += 1) {
    if (!(await focusNext(page, direction, scope))) break;
    await settleFrames(page);
    const stop = await focusStop(page);
    if (stop) stops.push(stop);
  }
  return stops;
}

export function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

/** A tall block at the end of `.main`, so any control in it can scroll to the top. */
export async function addScrollRoom(page: Page): Promise<void> {
  await page.locator('.main').evaluate((main) => {
    const spacer = document.createElement('div');
    spacer.style.blockSize = '3000px';
    main.append(spacer);
  });
}

/** Scroll `.main` so `target` sits entirely behind the sticky route nav. */
export async function behindRouteNav(page: Page, target: Locator): Promise<void> {
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
export async function gapBelowRouteNav(target: Locator): Promise<number> {
  return target.evaluate((element) => (
    element.getBoundingClientRect().top - (document.querySelector('.route-nav')?.getBoundingClientRect().bottom ?? 0)
  ));
}

/** Scroll the table so row `index` sits exactly behind the sticky header; returns its borrower id. */
export async function rowBehindHeader(page: Page, index: number): Promise<string> {
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

/** How far below `.main`'s scrollport top the walks up start. */
const WALK_START_DEPTH = 250;

/**
 * `.main` at its natural end and the table scroller at its middle, so the
 * rows run up under the sticky route nav. Returns the row checkbox a walk up
 * starts from, WALK_START_DEPTH below `.main`'s scrollport top.
 */
export async function tableUpUnderRouteNav(page: Page): Promise<Locator> {
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
      mainScrollTop: main.scrollTop,
      tableScrollTop: wrap.scrollTop,
      tableTopUnderNav: port.top < nav.getBoundingClientRect().bottom,
      startOffset: start ? start.top - (view.top + depth) : Number.NaN,
      start: below?.dataset.testid ?? '',
    };
  }, WALK_START_DEPTH);
  expect(placed.mainScrollTop, 'precondition: `.main` scrolls by itself at 1440x900').toBeGreaterThan(0);
  expect(placed.tableScrollTop, 'precondition: the table scroller sits part-way down').toBeGreaterThan(0);
  expect(placed.tableTopUnderNav, 'precondition: the table\'s top edge is up under the route nav').toBe(true);
  expect(placed.start, `precondition: a row checkbox ${WALK_START_DEPTH}px below the scrollport top`).not.toBe('');
  expect(Math.abs(placed.startOffset), 'precondition: the start sits at that depth').toBeLessThanOrEqual(1);
  return page.getByTestId(placed.start);
}

/** Scroll the table sideways so `target` sits fully under the pinned Approval column. */
export async function underPinnedColumn(target: Locator): Promise<void> {
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
export const ZERO_TABLE_BLOCK_MARGIN = '.lead-table__table tr, .lead-table__table tr * { scroll-margin-block-start: 0px !important; }';
/** Both of LeadTable.css's clearances: that margin, and the scroller's inline-end padding for the pin. */
export const ZERO_TABLE_CLEARANCE = `${ZERO_TABLE_BLOCK_MARGIN} .tbl-wrap { scroll-padding: 0px !important; }`;
/** 38-focus-clearance.css's mechanism: the nav's size as scroll-margin on what is outside it. */
export const ZERO_NAV_MARGIN = '.main * { scroll-margin-block-start: 0px !important; }';
/** Zeroes the pinned controls' own scroll-margin (LeadTable.css), leaving the scroller's padding. */
export const ZERO_PIN_MARGIN = '.lead-table__table .tbl-cell--approval * { scroll-margin-inline: 0px !important; }';
/** --focus-ring-width + --focus-ring-offset (tokens.css). */
export const FOCUS_RING_ROOM = 4;
