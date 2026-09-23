/**
 * Keyboard and screen-reader support for the geography map (audit a11y-04).
 *
 *  - Accessible names carry the same aggregates the hover card shows (count,
 *    average score, top segment), never borrower-level data, so a screen
 *    reader user gets the data without the mouse-only tooltip.
 *  - One roving tab stop per level: the states (and the ZIP tiles) are one
 *    Tab stop, and the arrow keys move between them. Same pattern as the
 *    Analytics equity scatter (`routes/analytics.equity-scatter.tsx`).
 *  - The hover card also opens on keyboard focus, anchored to the focused
 *    element's box instead of the pointer.
 */
import type { KeyboardEvent } from 'react';

/** Attribute every roving map unit (state path, ZIP tile button) carries. */
export const MAP_UNIT_ATTR = 'data-map-unit';

const fmt = (value: number) => value.toLocaleString('en-US');

export interface UnitFacts {
  count: number | null;
  avgScore: number | null;
  topSegment?: string;
  /** Unattended leads when the assignment overlay colours the map. */
  unattended?: number | null;
}

/** "N marketable borrowers, average opportunity score S, top segment X[, U unattended leads]". */
function describeFacts(facts: UnitFacts, noun: string): string {
  const parts = [facts.count !== null ? `${fmt(facts.count)} ${noun}` : `${noun}: unknown`];
  if (facts.avgScore !== null) parts.push(`average opportunity score ${fmt(facts.avgScore)}`);
  if (facts.topSegment) parts.push(`top segment ${facts.topSegment}`);
  if (typeof facts.unattended === 'number') parts.push(`${fmt(facts.unattended)} unattended leads`);
  return parts.join(', ');
}

export type StateLabelStatus = 'loading' | 'ready' | 'unavailable';

/**
 * Accessible name of one state path. Starts with the state name and a comma,
 * so a test or a screen-reader user can still find "Illinois" first.
 */
export function stateAriaLabel(
  name: string,
  facts: UnitFacts | undefined,
  status: StateLabelStatus,
  inFootprint: boolean,
): string {
  if (status === 'loading') return `${name}, loading borrower counts`;
  if (status === 'unavailable') return `${name}, borrower counts unavailable`;
  if (!facts) {
    return inFootprint ? `${name}, no borrower rollup` : `${name}, outside the Cotality evaluation scope`;
  }
  return `${name}, ${describeFacts(facts, 'marketable borrowers')}`;
}

export function zipAriaLabel(zip: string, facts: UnitFacts): string {
  return `ZIP ${zip}, ${describeFacts(facts, 'borrowers')}`;
}

/**
 * Arrow keys / Home / End move focus between the `[data-map-unit]` elements
 * inside the handler's element, wrapping at the ends. Anything else (Enter,
 * Space, Tab, Escape) is left to the unit's own handlers and the browser.
 */
export function moveRovingFocus(event: KeyboardEvent<Element>): void {
  const direction = event.key === 'ArrowRight' || event.key === 'ArrowDown'
    ? 1
    : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
      ? -1
      : 0;
  if (direction === 0 && event.key !== 'Home' && event.key !== 'End') return;
  const units = [...event.currentTarget.querySelectorAll<HTMLElement | SVGElement>(`[${MAP_UNIT_ATTR}]`)];
  const current = units.findIndex((unit) => unit === event.target);
  if (current < 0 || units.length === 0) return;
  event.preventDefault();
  const next = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? units.length - 1
      : (current + direction + units.length) % units.length;
  units[next]?.focus();
}

/** Where the hover card anchors for a focused unit: top centre of its box, in client coordinates. */
export function focusAnchor(element: Element): { x: number; y: number } {
  const box = element.getBoundingClientRect();
  return { x: box.left + box.width / 2, y: box.top };
}

/**
 * Open the card for a focused unit now, and re-anchor it on the next frame:
 * the focus event fires before the browser scrolls the unit into view, so
 * the first measurement can be off by the scroll distance.
 */
export function showCardOnFocus(element: Element, show: (anchor: { x: number; y: number }) => void): void {
  show(focusAnchor(element));
  window.requestAnimationFrame(() => {
    if (element.ownerDocument.activeElement === element) show(focusAnchor(element));
  });
}
