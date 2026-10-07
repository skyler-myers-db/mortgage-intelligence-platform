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
import { formatCount, formatNumber, ratePct, signedCount } from '../../lib/formatters';
import type { HoverScenario } from './USChoroplethMap.utils';

/** Attribute every map unit (state path, ZIP tile) carries. */
export const MAP_UNIT_ATTR = 'data-map-unit';

/**
 * Attribute a unit with borrowers in the selection carries (state:
 * addressable > 0; ZIP: addressable_borrowers > 0; never the fill value).
 * Only these are roving stops and controls (dataviz-10, WCAG 2.1.1).
 * deviation:map-escape-and-populated-roving.
 */
export const MAP_POPULATED_ATTR = 'data-populated';

export interface UnitFacts {
  count: number | null;
  avgScore: number | null;
  topSegment?: string;
  /** Unattended leads when the assignment overlay colours the map. */
  unattended?: number | null;
}

/** "N marketable borrowers, average opportunity score S, top segment X[, U unattended leads]". */
function describeFacts(facts: UnitFacts, noun: string): string {
  const parts = [facts.count !== null ? `${formatCount(facts.count)} ${noun}` : `${noun}: unknown`];
  if (facts.avgScore !== null) parts.push(`average opportunity score ${formatNumber(facts.avgScore)}`);
  if (facts.topSegment) parts.push(`top segment ${facts.topSegment}`);
  if (typeof facts.unattended === 'number') parts.push(`${formatCount(facts.unattended)} unattended leads`);
  return parts.join(', ');
}

export type StateLabelStatus = 'loading' | 'ready' | 'unavailable';

/**
 * The Rate Lever suffix of a state's name (wow-stage-1): the count at the
 * scenario rate and its change versus today, or today's count at step 0.
 * The cohort note is the path's description (USChoroplethMapStates).
 */
function scenarioSuffix(scenario: HoverScenario | undefined): string {
  if (!scenario) return '';
  return scenario.step === 0
    ? `; in the money today: ${formatCount(scenario.today)}`
    : `; in the money at ${ratePct(scenario.ratePct)}: ${formatCount(scenario.atStep)} (${signedCount(scenario.change)} vs today)`;
}

/**
 * Accessible name of one state path: the state name, a colon, then the facts
 * (the Genie answer map's `Name: value` form). The colon keeps "Washington:"
 * apart from "Washington, DC:" for a prefix match, and a screen-reader user
 * still hears the name first.
 */
export function stateAriaLabel(
  name: string,
  facts: UnitFacts | undefined,
  status: StateLabelStatus,
  inFootprint: boolean,
  scenario?: HoverScenario,
): string {
  if (status === 'loading') return `${name}: loading borrower counts`;
  if (status === 'unavailable') return `${name}: borrower counts unavailable`;
  if (!facts) {
    return inFootprint ? `${name}: no borrower rollup` : `${name}: outside the Cotality evaluation scope`;
  }
  return `${name}: ${describeFacts(facts, 'marketable borrowers')}${scenarioSuffix(scenario)}`;
}

export function zipAriaLabel(zip: string, facts: UnitFacts): string {
  return `ZIP ${zip}: ${describeFacts(facts, 'borrowers')}`;
}

/**
 * The parts of a keydown moveRovingFocus reads: a React KeyboardEvent (the
 * state and tile stages) or a native one (the ZCTA stage's single listener)
 * both fit; the stage the handler sits on is `currentTarget`.
 */
export interface RovingKeyEvent {
  key: string;
  target: EventTarget | null;
  currentTarget: EventTarget | null;
  preventDefault: () => void;
}

/**
 * Arrow keys / Home / End move focus between the populated
 * `[data-map-unit][data-populated]` elements inside the handler's element,
 * wrapping at the ends; Home / End go to the first / last populated unit.
 * Anything else (Enter, Space, Tab, Escape) is left to the unit's own
 * handlers and the browser.
 */
export function moveRovingFocus(event: RovingKeyEvent): void {
  const direction = event.key === 'ArrowRight' || event.key === 'ArrowDown'
    ? 1
    : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
      ? -1
      : 0;
  if (direction === 0 && event.key !== 'Home' && event.key !== 'End') return;
  // The stage the handler is registered on (React or native: always an element).
  const units = [...(event.currentTarget as Element).querySelectorAll<HTMLElement | SVGElement>(`[${MAP_UNIT_ATTR}][${MAP_POPULATED_ATTR}]`)];
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

/** The state stage's description: how many drawn states the keys skip (none: no note). */
export function skippedStatesNote(count: number): string {
  const one = count === 1;
  return `${one ? '1 state has' : `${formatCount(count)} states have`} no borrowers in this selection and ${
    one ? 'is' : 'are'
  } skipped; the table view lists them.`;
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

/**
 * Marks a control OUTSIDE the map that belongs to it: activating it ends the
 * drill and removes the control itself (Segment Intelligence's "Clear
 * geography" in the chip row above the map). Only such a control, or one
 * inside the map, hands focus to the map when the drill ends.
 */
export const MAP_DRILL_EXIT_ATTR = 'data-map-drill-exit';

/**
 * Whether the drill that just ended was ended by the map or by a control that
 * belongs to it: the element that last took focus is inside `mapRoot`, or
 * carries `MAP_DRILL_EXIT_ATTR`. A page-level control that also resets the
 * drill ("Clear filters" in a route hero, which disables itself) is not the
 * map's to answer, so the map never pulls focus, and the page scroll with it,
 * across the page to its own crumb.
 */
export function drillExitOriginatedInMap(lastFocused: Element | null, mapRoot: Element | null): boolean {
  if (!lastFocused) return false;
  if (lastFocused.closest(`[${MAP_DRILL_EXIT_ATTR}]`)) return true;
  return mapRoot !== null && mapRoot.contains(lastFocused);
}

/**
 * Give focus to `target` after a drill unmounted the control that held it
 * (the state path, the table row's button): without this, focus falls to
 * `<body>` and a keyboard or screen-reader user is thrown back to the top of
 * the document. It only claims focus that is lost (on `<body>`) or parked on
 * `interim` (the warming / failed stage a pending drill took it to), so a
 * rollup that resolves late never pulls focus away from where the user
 * moved it. Returns whether `target` now has focus.
 */
export function claimDrillFocus(target: HTMLElement | SVGElement | null, interim: Element | null = null): boolean {
  if (!target) return false;
  const doc = target.ownerDocument;
  const active = doc.activeElement;
  if (active && active !== doc.body && active !== interim) return false;
  target.focus();
  return doc.activeElement === target;
}
