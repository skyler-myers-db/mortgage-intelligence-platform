import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useExitRetained } from '../hooks/useExitRetained';
import type { DrawerSource } from './AppContext';
import { freshnessBucket, FRESHNESS_LABEL } from './freshness';
import { preloadEvidenceDrawerBody } from './mortgage/evidenceDrawerBodyLoader';
import './EvidenceHoverCard.css';

/**
 * Evidence hover micro-preview (re-audit #4 Buyer-Wow #8, done the safe
 * way; timing, placement and exit reworked by 2026-09-21 audit motion-10 /
 * css-10). It is a PASSIVE preview: aria-hidden (the chip already carries an
 * accessible name and the freshness dot its own aria-label), pointer-events
 * none, and it never requests anything. Click still opens the full drawer.
 * Touch devices (no hover / focus tooltip) are unaffected.
 *
 * Top layer: the card is a `popover="manual"` portal, shown with
 * showPopover() in a layout effect after it mounts, so it sits above every
 * overflow clip and stacking context without a z-index race. Manual, never
 * `auto` (that light-dismisses an open menu) and never `hint`.
 *
 * Placement, two modes picked in JS and recorded as `data-anchored`:
 *  - CSS anchor positioning (`CSS.supports('anchor-name: --a')`): while its
 *    card is open the chip carries a per-instance `anchor-name` and the card
 *    the matching `position-anchor`; EvidenceHoverCard.css places it above
 *    the chip, flips it below when there is no room and hides it once the
 *    chip scrolls out of its clip. It follows the chip on scroll.
 *  - Elsewhere, fixed viewport coordinates from the chip's rect, placed
 *    above or below by the card's MEASURED height (a layout effect, before
 *    paint, while the card is still visibility:hidden), and hidden on any
 *    scroll or resize instead of drifting.
 *
 * Timing: hover waits SHOW_DELAY_MS so sweeping the pointer across a table
 * does not pop a card per chip; once a card has closed, the next one opens
 * at once for REOPEN_GRACE_MS (a user scanning chips one by one). Keyboard
 * focus shows immediately. Closing fades out over --dur-instant through
 * hooks/useExitRetained (instant under reduced motion and where no
 * transition runs, such as the test DOM).
 *
 * Intent: hovering or focusing a chip preloads the evidence drawer's lazy
 * body chunk (audit bundle-04), so a click opens it without a loading state.
 * The chunk only, never data: the drawer's reads start once it is open.
 */

const SHOW_DELAY_MS = 350;
const REOPEN_GRACE_MS = 300;
/** Gap between the chip and the card, and the room kept above the card. */
const CARD_GAP_PX = 8;
const VIEWPORT_MARGIN_PX = 4;

/** When the last card began to close (module-wide; read only in handlers). */
let lastClosedAt = Number.NEGATIVE_INFINITY;

interface RectPlacement {
  x: number;
  y: number;
  side: 'above' | 'below';
}

interface OpenCard {
  anchored: boolean;
  /** Rect mode only: null until the card has been measured. */
  placement: RectPlacement | null;
}

export interface EvidenceHoverApi {
  anchorRef: (el: HTMLElement | null) => void;
  anchorHandlers: {
    onMouseEnter: () => void;
    onMouseLeave: () => void;
    onFocus: () => void;
    onBlur: () => void;
  };
  hoverCard: ReactNode;
}

export interface EvidenceHoverOptions {
  /** Call-to-action line; defaults to the evidence-drawer CTA of an EvidenceChip. */
  cta?: string;
}

function supportsAnchorPositioning(): boolean {
  return typeof CSS !== 'undefined' && CSS.supports('anchor-name: --a');
}

function measuredPlacement(anchor: HTMLElement, card: HTMLElement): RectPlacement {
  const rect = anchor.getBoundingClientRect();
  const height = card.getBoundingClientRect().height;
  const above = rect.top >= height + CARD_GAP_PX + VIEWPORT_MARGIN_PX;
  return {
    x: rect.left + rect.width / 2,
    y: above ? rect.top - CARD_GAP_PX : rect.bottom + CARD_GAP_PX,
    side: above ? 'above' : 'below',
  };
}

export function useEvidenceHoverCard(source?: DrawerSource, options: EvidenceHoverOptions = {}): EvidenceHoverApi {
  const anchorElRef = useRef<HTMLElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const [open, setOpen] = useState<OpenCard | null>(null);
  // The card stays mounted (`.is-closing`) until its own exit transition ends.
  const shown = useExitRetained(open, cardRef);
  const anchorName = `--evidence-anchor${useId().replace(/[^\w-]/g, '-')}`;

  const anchorRef = useCallback((el: HTMLElement | null) => {
    anchorElRef.current = el;
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const show = useCallback(() => {
    if (!anchorElRef.current) return;
    setOpen({ anchored: supportsAnchorPositioning(), placement: null });
  }, []);

  const hide = useCallback(() => {
    clearTimer();
    if (open) lastClosedAt = Date.now();
    setOpen(null);
  }, [clearTimer, open]);

  const onMouseEnter = useCallback(() => {
    if (!source) return;
    preloadEvidenceDrawerBody();
    clearTimer();
    if (Date.now() - lastClosedAt < REOPEN_GRACE_MS) {
      show();
      return;
    }
    timerRef.current = window.setTimeout(show, SHOW_DELAY_MS);
  }, [clearTimer, show, source]);

  const onFocus = useCallback(() => {
    if (!source) return;
    preloadEvidenceDrawerBody();
    // Keyboard focus shows immediately (no hover-intent delay needed).
    clearTimer();
    show();
  }, [clearTimer, show, source]);

  // Before paint, once per change: promote the card to the top layer; in
  // anchor mode the chip carries the name only while its card is mounted
  // (its exit fade included); in rect mode, measure the rendered card, then
  // place it (it stays visibility:hidden until then).
  useLayoutEffect(() => {
    const card = cardRef.current;
    const anchor = anchorElRef.current;
    if (!shown || !card || !anchor) return undefined;
    if (card.showPopover && !card.matches(':popover-open')) card.showPopover();
    if (shown.anchored) {
      anchor.style.setProperty('anchor-name', anchorName);
      return () => {
        anchor.style.removeProperty('anchor-name');
      };
    }
    // An open card is measured in the commit that mounts it, so a closing
    // (retained) card always has its placement already.
    if (!shown.placement) setOpen({ anchored: false, placement: measuredPlacement(anchor, card) });
    return undefined;
  }, [anchorName, shown]);

  // Rect mode only: a fixed card would drift on scroll / resize, so it hides
  // (it re-shows on the next hover). An anchored card follows its chip.
  useEffect(() => {
    if (!open || open.anchored) return undefined;
    const onMove = () => hide();
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [hide, open]);

  // Clean up a pending timer on unmount.
  useEffect(() => () => clearTimer(), [clearTimer]);

  const bucket = freshnessBucket(source?.updatedAt);
  const signal = source?.signals?.[0];
  const placement = shown?.placement ?? null;
  const className = `evidence-hovercard${placement ? ` evidence-hovercard--${placement.side}` : ''}${open === null ? ' is-closing' : ''}`;

  const hoverCard =
    shown && source && typeof document !== 'undefined'
      ? createPortal(
          <div
            ref={cardRef}
            className={className}
            popover="manual"
            data-anchored={shown.anchored ? '' : undefined}
            style={
              shown.anchored
                ? { positionAnchor: anchorName }
                : placement
                  ? { left: placement.x, top: placement.y }
                  : { visibility: 'hidden' }
            }
            aria-hidden="true"
          >
            <div className="evidence-hovercard__title">{source.title}</div>
            {(bucket || source.updatedAt || source.eventDate) && (
              <div className="evidence-hovercard__row">
                {bucket && (
                  <span className={`evidence-hovercard__dot evidence-hovercard__dot--${bucket}`} />
                )}
                <span>
                  {bucket ? FRESHNESS_LABEL[bucket] : 'Evidence'}
                  {source.updatedAt
                    ? ` · refreshed ${source.updatedAt}`
                    : source.eventDate
                      ? ` · event ${source.eventDate}`
                      : ''}
                </span>
              </div>
            )}
            {signal && (
              <div className="evidence-hovercard__signal">
                <span className="evidence-hovercard__signal-label">{signal.label}</span>
                <span className="evidence-hovercard__signal-value mono">{signal.value}</span>
              </div>
            )}
            <div className="evidence-hovercard__cta">{options.cta ?? 'Click to open full evidence →'}</div>
          </div>,
          document.body,
        )
      : null;

  return {
    anchorRef,
    anchorHandlers: { onMouseEnter, onMouseLeave: hide, onFocus, onBlur: hide },
    hoverCard,
  };
}
