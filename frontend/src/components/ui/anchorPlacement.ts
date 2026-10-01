/**
 * Placement and timing shared by the evidence hover card and the Tooltip
 * primitive (2026-09-21 audit critic-08; moved verbatim out of
 * EvidenceHoverCard.tsx so both surfaces place and time the same way).
 *
 * Two placement modes, picked in JS and recorded as `data-anchored` on the
 * popup:
 *  - CSS anchor positioning (`CSS.supports('anchor-name: --a')`): the
 *    trigger carries a per-instance `anchor-name` while its popup is mounted
 *    and the popup the matching `position-anchor`; the popup's stylesheet
 *    places it.
 *  - Elsewhere, fixed viewport coordinates from the trigger's rect, placed
 *    above or below by the popup's MEASURED height (`measuredPlacement`).
 *
 * Timing: hover waits SHOW_DELAY_MS; once any popup has closed, the next one
 * opens at once for REOPEN_GRACE_MS. The close clock is module-wide, so the
 * skip-delay spans evidence cards and tooltips alike (a user scanning a row
 * of controls).
 */

export const SHOW_DELAY_MS = 350;
export const REOPEN_GRACE_MS = 300;
/** Gap between the trigger and the popup, and the room kept above the popup. */
export const CARD_GAP_PX = 8;
export const VIEWPORT_MARGIN_PX = 4;

/** When the last hover popup began to close (module-wide; read only in handlers). */
let lastClosedAt = Number.NEGATIVE_INFINITY;

/** Record that a hover popup just began to close (starts the reopen grace). */
export function markHoverClosed(): void {
  lastClosedAt = Date.now();
}

/** True inside the reopen grace after the last popup closed: open at once. */
export function inReopenGrace(): boolean {
  return Date.now() - lastClosedAt < REOPEN_GRACE_MS;
}

export interface RectPlacement {
  x: number;
  y: number;
  side: 'above' | 'below';
}

export function supportsAnchorPositioning(): boolean {
  return typeof CSS !== 'undefined' && CSS.supports('anchor-name: --a');
}

export function measuredPlacement(
  anchor: HTMLElement,
  card: HTMLElement,
  gap: number = CARD_GAP_PX,
  margin: number = VIEWPORT_MARGIN_PX,
): RectPlacement {
  const rect = anchor.getBoundingClientRect();
  const height = card.getBoundingClientRect().height;
  const above = rect.top >= height + gap + margin;
  return {
    x: rect.left + rect.width / 2,
    y: above ? rect.top - gap : rect.bottom + gap,
    side: above ? 'above' : 'below',
  };
}
