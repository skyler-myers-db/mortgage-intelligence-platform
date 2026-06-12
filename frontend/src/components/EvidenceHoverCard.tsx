import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { DrawerSource } from './AppContext';
import { freshnessBucket, FRESHNESS_LABEL } from './freshness';

/**
 * Evidence hover micro-preview (re-audit #4 Buyer-Wow #8, done the safe
 * way). EvidenceChip lives inside overflow:auto scroll containers (the lead
 * table), so a CSS-only popover would clip; this renders through a PORTAL to
 * document.body with viewport-fixed coordinates, so it never clips and never
 * shifts layout. It is a progressive enhancement over the chip's existing
 * behavior: click still opens the full drawer; the card is purely visual
 * (aria-hidden — the chip already carries an accessible name and the
 * freshness dot its own aria-label) and shows on hover OR keyboard focus.
 * Reduced-motion is honored in CSS. Touch devices (no hover/focus tooltip)
 * are unaffected — they tap to open the drawer as before.
 */

const SHOW_DELAY_MS = 110;
const ESTIMATED_CARD_H = 132;

interface HoverCoords {
  x: number;
  y: number;
  placement: 'above' | 'below';
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

export function useEvidenceHoverCard(source?: DrawerSource): EvidenceHoverApi {
  const anchorElRef = useRef<HTMLElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const [coords, setCoords] = useState<HoverCoords | null>(null);

  const anchorRef = useCallback((el: HTMLElement | null) => {
    anchorElRef.current = el;
  }, []);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const computeAndShow = useCallback(() => {
    const el = anchorElRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const above = rect.top > ESTIMATED_CARD_H + 12;
    setCoords({
      x: rect.left + rect.width / 2,
      y: above ? rect.top - 8 : rect.bottom + 8,
      placement: above ? 'above' : 'below',
    });
  }, []);

  const hide = useCallback(() => {
    clearTimer();
    setCoords(null);
  }, []);

  const onMouseEnter = useCallback(() => {
    if (!source) return;
    clearTimer();
    timerRef.current = window.setTimeout(computeAndShow, SHOW_DELAY_MS);
  }, [source, computeAndShow]);

  const onFocus = useCallback(() => {
    if (!source) return;
    // Keyboard focus shows immediately (no hover-intent delay needed).
    clearTimer();
    computeAndShow();
  }, [source, computeAndShow]);

  // A fixed-position card would drift on scroll/resize — hide it instead of
  // chasing the anchor (the card is transient and re-shows on next hover).
  useEffect(() => {
    if (!coords) return;
    const onMove = () => hide();
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [coords, hide]);

  // Clean up a pending timer on unmount.
  useEffect(() => () => clearTimer(), []);

  const bucket = freshnessBucket(source?.updatedAt);
  const signal = source?.signals?.[0];

  const hoverCard =
    coords && source && typeof document !== 'undefined'
      ? createPortal(
          <div
            className={`evidence-hovercard evidence-hovercard--${coords.placement}`}
            style={{ left: coords.x, top: coords.y }}
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
            <div className="evidence-hovercard__cta">Click to open full evidence →</div>
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
