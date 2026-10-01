import { useLayoutEffect, type RefObject } from 'react';

/** The sticky thead's block size, on `.tbl-wrap` (LeadTable.css). */
export const TABLE_HEAD_BLOCK_VAR = '--tbl-head-block-size';

/** The pinned Approval cell (LeadTableRow): its controls are always in view. */
const PINNED_CELL = '.tbl-cell--approval';

/** The control lies fully inside the scroller's scrollport (1px of rounding slack). */
function fullyInside(control: Element, wrap: HTMLElement): boolean {
  const box = control.getBoundingClientRect();
  const port = wrap.getBoundingClientRect();
  const left = port.left + wrap.clientLeft;
  return box.left >= left - 1 && box.right <= left + wrap.clientWidth + 1;
}

/**
 * WebKit 26 scrolls `.tbl-wrap` to its inline end when a control in the
 * pinned Approval column takes focus (element.focus() moved scrollLeft
 * 0 -> 284 with the Console open; manual check 2026-09-30), although the
 * column is sticky and the control was already in view: the row's borrower
 * id then slid out of sight. Chromium does not while LeadTable.css's
 * scroll-margin holds, so there this is a no-op.
 *
 * The scroller's offset is recorded when it attaches, on its passive scroll
 * events (which an engine dispatches in a later frame, so a reveal scroll
 * made during a focus is not recorded yet when the focus events run) and on
 * a key or pointer press inside it (capture), which precede a focus the
 * reader causes. On a focusin inside the pinned cell, that recorded offset
 * is the one before the focus, on either side of the focus events the
 * engine scrolls: WebKit scrolls after them, Chromium (with the margin
 * gone) before them. It is restored twice: in a microtask (a programmatic
 * focus() has scrolled by the time its caller's task ends) and in the next
 * animation frame (a Tab or a click focus scrolls after the focus events).
 * Each restore runs only if the offset moved, and is kept only if the
 * control is still fully inside the scrollport at the restored offset;
 * otherwise the engine's scroll stands. A horizontal scroll made by script
 * in the same frame as a pinned focus, with no scroll event or press
 * between, would be undone; nothing in the app scrolls the table
 * horizontally. No style is written; the listeners go with the table
 * (attached from useTableScrollClearance's effect).
 *
 * @returns the detach.
 */
function guardPinnedFocusScroll(wrap: HTMLElement): () => void {
  let last = wrap.scrollLeft;
  let frame = 0;
  const record = () => {
    last = wrap.scrollLeft;
  };
  const restore = (control: Element, before: number) => {
    const moved = wrap.scrollLeft;
    if (Math.abs(moved - before) <= 1) return;
    wrap.scrollLeft = before;
    if (!fullyInside(control, wrap)) wrap.scrollLeft = moved;
    record();
  };
  const onFocusIn = (event: Event) => {
    const control = event.target instanceof Element ? event.target : null;
    if (!control?.closest(PINNED_CELL)) return;
    const before = last;
    queueMicrotask(() => restore(control, before));
    window.cancelAnimationFrame(frame);
    frame = window.requestAnimationFrame(() => restore(control, before));
  };
  // [type, listener, capture]; the scroll listener is passive.
  const listeners: Array<[string, (event: Event) => void, boolean]> = [
    ['scroll', record, false],
    ['keydown', record, true],
    ['pointerdown', record, true],
    ['focusin', onFocusIn, false],
  ];
  for (const [type, listener, capture] of listeners) wrap.addEventListener(type, listener, { capture, passive: true });
  return () => {
    for (const [type, listener, capture] of listeners) wrap.removeEventListener(type, listener, capture);
    window.cancelAnimationFrame(frame);
  };
}

function writePx(element: HTMLElement, property: string, size: number): void {
  const next = `${Math.ceil(size)}px`;
  if (element.style.getPropertyValue(property) !== next) element.style.setProperty(property, next);
}

/**
 * Keep keyboard focus clear of the ranked-borrower table's sticky thead
 * (audit a11y-v2; WCAG 2.2 SC 2.4.11 Focus Not Obscured, technique C43).
 *
 * The table scroller (`.tbl-wrap`) has a sticky thead at its top and, on the
 * Lead Queue, the pinned Approval column at its end edge. A row control that
 * takes focus from outside the view (Shift+Tab up the rows, Tab across a row
 * to its Approval cell with the Console open) was scrolled only to the
 * scroller's edge, behind one of them. LeadTable.css clears them by their
 * sizes plus the focus ring: the table's focus targets take scroll-margin
 * at the block start (the larger of the thead's size and the sticky route
 * nav's, which 38-focus-clearance.css sets on `.main` at its one-line
 * size), and the scroller takes scroll-padding at the inline end for the
 * pin. The pin's size is its column's declared width (the table is
 * `table-layout: fixed`), so only the thead, whose labels can wrap, is
 * measured here and written as a custom property; the sheet declares a
 * fallback, so nothing depends on this having run.
 *
 * Cost (wave-4b integration): the write is registered `inherits: false`
 * in LeadTable.css, so it re-styles the scroller alone. As an ordinary,
 * inherited custom property, each write re-styled every row of the table,
 * and with the pin written too the cold Lead Queue lost ~340 ms of TBT at
 * 4x CPU (measured by bisect; focus-clearance-cost.fixture.spec.ts pins the
 * re-styled element count). The first measurement is also taken in the
 * next animation frame rather than during the commit, where reading layout
 * forced a synchronous layout before the first paint (+~480 ms LCP).
 *
 * Measured in the next frame after mount and whenever the thead resizes,
 * never inside the ResizeObserver callback (as useLeadTableFillHeight does),
 * so it cannot start a resize loop. The property is removed on unmount.
 *
 * @param layoutKey re-observes when the columns change (the view preset
 *   re-renders the header cells).
 */
export function useTableScrollClearance(wrapRef: RefObject<HTMLElement | null>, layoutKey: string): void {
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return undefined;
    const unguard = guardPinnedFocusScroll(wrap);
    const head = wrap.querySelector<HTMLElement>('thead');
    if (!head) return unguard;

    const measure = () => writePx(wrap, TABLE_HEAD_BLOCK_VAR, head.getBoundingClientRect().height);
    let frame = 0;
    const schedule = () => {
      if (frame !== 0) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    };
    schedule();

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    observer?.observe(head);
    return () => {
      unguard();
      observer?.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
      wrap.style.removeProperty(TABLE_HEAD_BLOCK_VAR);
    };
  }, [wrapRef, layoutKey]);
}
