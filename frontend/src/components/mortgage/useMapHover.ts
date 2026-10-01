/**
 * useMapHover — the geography map's hover / focus card, delegated to each
 * stage's root (D-dataviz-geo-d1, audit runtime-07; deviation:map-tip-top-layer).
 *
 * The only React state is WHICH unit the card describes:
 * `{ level, id, source }`. Pointer tracking never renders: the pointer
 * position is kept in a ref and written in a requestAnimationFrame as
 * `left` / `top` on the tip element alone, with the card's existing clamp
 * (`max(160, min(innerWidth - 160, x))`, `top = y - 4`). No custom property
 * is ever written on `.map-wrap`, `.map-levels` or any ancestor of the stage:
 * a JS-written property there restyles the whole subtree (wave 4b,
 * focus-clearance-cost).
 *
 * A stage spreads `stage.handlers(level, onUnitFocus)` on its root (`<svg>`,
 * `<ul>`): the handlers resolve the unit with `closest('[data-map-unit]')`
 * inside that root, so 51 paths (and, later, a ZCTA stage's thousands of
 * units) carry no per-unit hover closures.
 *
 *  - pointerover on a unit shows its card; on the root's own background, or
 *    the root's pointerleave, clears it;
 *  - pointermove only records the point and schedules a frame;
 *  - focus calls the stage's `onUnitFocus` (its roving memory), then opens
 *    the card anchored on the unit's box; the next-frame re-anchor (the
 *    focus fires before the browser scrolls the unit into view) only places
 *    the tip, it never sets state for the same unit;
 *  - blur keeps the card when focus moves to another unit of the same root.
 */
import { useCallback, useMemo, useRef, useState, type FocusEvent, type PointerEvent, type RefObject } from 'react';
import { MAP_UNIT_ATTR, showCardOnFocus } from './USChoroplethMap.a11y';
import type { Level } from './USChoroplethMap.utils';

export type MapHoverSource = 'pointer' | 'focus';

/** The unit the card describes. */
export interface MapHovered {
  level: Level;
  id: string;
  source: MapHoverSource;
}

/** Spread on a stage's root element. */
export interface MapStageHandlers {
  onPointerOver: (event: PointerEvent<Element>) => void;
  onPointerMove: (event: PointerEvent<Element>) => void;
  onPointerLeave: () => void;
  onFocus: (event: FocusEvent<Element>) => void;
  onBlur: (event: FocusEvent<Element>) => void;
}

/** What a stage receives: its root's handlers, bound to its level and roving memory. */
export interface MapHoverStage {
  handlers: (level: Level, onUnitFocus: (id: string) => void) => MapStageHandlers;
}

export interface MapHover {
  hovered: MapHovered | null;
  stage: MapHoverStage;
  hide: () => void;
  /** The tip element (USChoroplethMapTooltip renders it). */
  tipRef: RefObject<HTMLDivElement | null>;
  /** Write the current point as the tip's `left` / `top`. */
  placeTip: (element: HTMLElement) => void;
}

interface TipPoint {
  x: number;
  y: number;
}

/** The card's horizontal clamp: it never leaves a 160px gutter. */
export function clampTipX(x: number): number {
  return Math.max(160, Math.min(window.innerWidth - 160, x));
}

/** The map unit an event target sits in, within `root`; null for the root's own background. */
export function unitWithin(target: EventTarget | null, root: Element): Element | null {
  if (!(target instanceof Element)) return null;
  const unit = target.closest(`[${MAP_UNIT_ATTR}]`);
  return unit && root.contains(unit) ? unit : null;
}

const sameUnit = (a: MapHovered | null, b: MapHovered) =>
  a !== null && a.level === b.level && a.id === b.id && a.source === b.source;

export function useMapHover(): MapHover {
  const [hovered, setHovered] = useState<MapHovered | null>(null);
  // The card's unit as the handlers last set it (state is read by render only).
  const shownRef = useRef<MapHovered | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const pointRef = useRef<TipPoint>({ x: 0, y: 0 });
  const frameRef = useRef(0);

  const placeTip = useCallback((element: HTMLElement) => {
    const { x, y } = pointRef.current;
    element.style.left = `${clampTipX(x)}px`;
    element.style.top = `${y - 4}px`;
  }, []);

  const hide = useCallback(() => {
    shownRef.current = null;
    setHovered(null);
  }, []);

  const stage = useMemo<MapHoverStage>(() => {
    const schedulePlacement = () => {
      if (frameRef.current) return;
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = 0;
        const tip = tipRef.current;
        if (tip) placeTip(tip);
      });
    };
    // Sets state only when the card's unit (or how it was reached) changes.
    const show = (next: MapHovered) => {
      if (sameUnit(shownRef.current, next)) return;
      shownRef.current = next;
      setHovered(next);
    };
    return {
      handlers: (level, onUnitFocus) => ({
        onPointerOver: (event) => {
          const unit = unitWithin(event.target, event.currentTarget);
          if (!unit) {
            hide();
            return;
          }
          pointRef.current = { x: event.clientX, y: event.clientY };
          show({ level, id: unit.getAttribute(MAP_UNIT_ATTR) ?? '', source: 'pointer' });
          schedulePlacement();
        },
        onPointerMove: (event) => {
          pointRef.current = { x: event.clientX, y: event.clientY };
          schedulePlacement();
        },
        onPointerLeave: () => hide(),
        onFocus: (event) => {
          const unit = unitWithin(event.target, event.currentTarget);
          if (!unit) return;
          const id = unit.getAttribute(MAP_UNIT_ATTR) ?? '';
          onUnitFocus(id);
          showCardOnFocus(unit, (anchor) => {
            pointRef.current = anchor;
            // A new unit renders its card (the tooltip places it before it
            // opens); the same unit (the next-frame re-anchor, or a click
            // that focused the hovered unit) only moves the open tip.
            show({ level, id, source: 'focus' });
            const tip = tipRef.current;
            if (tip) placeTip(tip);
          });
        },
        onBlur: (event) => {
          if (unitWithin(event.relatedTarget, event.currentTarget)) return;
          hide();
        },
      }),
    };
  }, [hide, placeTip]);

  return { hovered, stage, hide, tipRef, placeTip };
}
