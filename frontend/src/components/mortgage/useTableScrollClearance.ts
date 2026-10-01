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

/** How long a key or pointer press stays the offset of the focus it causes. */
const PRESS_WINDOW_MS = 500;

/**
 * WebKit 26 scrolls `.tbl-wrap` to its inline end when a control in the
 * pinned Approval column takes focus (element.focus() moved scrollLeft
 * 0 -> 284 with the Console open; manual check 2026-09-30), although the
 * column is sticky and the control was already in view: the row's borrower
 * id then slid out of sight. Chromium does not while LeadTable.css's
 * scroll-margin holds, so there this is a no-op.
 *
 * The offset to keep is the one the reader had before the focus: the offset
 * at a key or pointer press inside the table (capture) within the last
 * PRESS_WINDOW_MS, which precedes a focus the reader causes even when an
 * engine reveals before the focus events; otherwise the offset at the
 * focusin itself, because WebKit reveals after the focus events. It is never
 * an offset recorded from scroll events: the W5a integration's CI run showed
 * Linux WebKit and Chromium dispatch a script scroll's event after a focus
 * made two frames later, so a recorded offset could be stale, and the guard
 * then moved the table itself (scrollLeft 142 -> 0, no engine scroll at all).
 *
 * It is restored in a microtask (a programmatic focus() has scrolled by the
 * time its caller's task ends), in the next animation frame (a Tab or a
 * click focus scrolls after the focus events) and on the first scroll event
 * up to the frame after that one (WebKit's reveal can land after the restore
 * frame); a key or pointer press, a wheel or a touch in between is the
 * reader moving the table, and ends the watch, as does the frame after.
 * Each restore runs only
 * if the offset moved, and is kept only if the control is still fully
 * inside the scrollport at the restored offset; otherwise the engine's
 * scroll stands. No style is written; the listeners go with the table
 * (attached from useTableScrollClearance's effect).
 *
 * @returns the detach.
 */
function guardPinnedFocusScroll(wrap: HTMLElement): () => void {
  let pressed: { at: number; offset: number } | null = null;
  let watching: { control: Element; before: number } | null = null;
  let frame = 0;
  const onPress = (event: Event) => {
    watching = null;
    pressed = { at: event.timeStamp, offset: wrap.scrollLeft };
  };
  const onIntent = () => {
    watching = null;
  };
  const restore = (control: Element, before: number) => {
    const moved = wrap.scrollLeft;
    if (Math.abs(moved - before) <= 1) return;
    wrap.scrollLeft = before;
    if (!fullyInside(control, wrap)) wrap.scrollLeft = moved;
  };
  const onScroll = () => {
    const watch = watching;
    if (!watch) return;
    watching = null;
    restore(watch.control, watch.before);
  };
  const onFocusIn = (event: Event) => {
    const control = event.target instanceof Element ? event.target : null;
    const press = pressed;
    pressed = null;
    watching = null;
    if (!control?.closest(PINNED_CELL)) return;
    const before = press && event.timeStamp - press.at <= PRESS_WINDOW_MS ? press.offset : wrap.scrollLeft;
    const watch = { control, before };
    watching = watch;
    queueMicrotask(() => restore(control, before));
    window.cancelAnimationFrame(frame);
    frame = window.requestAnimationFrame(() => {
      restore(control, before);
      // The reveal's scroll event lands by the next frame at the latest.
      window.requestAnimationFrame(() => {
        if (watching === watch) watching = null;
      });
    });
  };
  // [type, listener, capture]; all passive.
  const listeners: Array<[string, (event: Event) => void, boolean]> = [
    ['keydown', onPress, true],
    ['pointerdown', onPress, true],
    ['wheel', onIntent, true],
    ['touchstart', onIntent, true],
    ['scroll', onScroll, false],
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
