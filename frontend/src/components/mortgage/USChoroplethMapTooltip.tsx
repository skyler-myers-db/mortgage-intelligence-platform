import { useLayoutEffect, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import type { MapCard } from './USChoroplethMap.hover';
import { MapTipBody } from './USChoroplethMapTipBody';

/**
 * Floating map hover card. BEM block: `map-tip` (prototype
 * design_files/Module 0 Prototype.html ~L1875-1878); its content is
 * MapTipBody.
 *
 * deviation:map-tip-top-layer (audit css-03 / runtime-07, D-dataviz-geo-d1).
 * The prototype's tip is an absolutely positioned div inside the map. Ours is
 * still portaled to document.body (`.map-wrap { overflow: hidden }` would
 * clip it), and is now a `popover="manual"` element promoted to the top
 * layer, so the floating Genie panel, the drawer and any other overlay never
 * cover it and a modal dialog's inert backdrop does not swallow it (the
 * EvidenceHoverCard pattern). It is mounted only while a card exists. Before
 * paint, each time the hovered unit changes, the tip is placed FIRST, then
 * promoted, so it never paints at the previous unit's point. Pointer moves
 * never render this component: useMapHover writes `left` / `top` on the
 * element in a frame. Without the Popover API the element stays a fixed,
 * z-indexed div (08-map.css keeps the fallback).
 */

interface USChoroplethMapTooltipProps {
  card: MapCard;
  /** The hovered unit (`level:id`): the tip is re-placed when it changes. */
  unitKey: string;
  tipRef: RefObject<HTMLDivElement | null>;
  placeTip: (element: HTMLElement) => void;
  activeSegNames: Set<string> | null;
}

export function USChoroplethMapTooltip({ card, unitKey, tipRef, placeTip, activeSegNames }: USChoroplethMapTooltipProps) {
  useLayoutEffect(() => {
    const tip = tipRef.current;
    if (!tip) return;
    placeTip(tip);
    if (tip.showPopover && !tip.matches(':popover-open')) tip.showPopover();
  }, [placeTip, tipRef, unitKey]);
  return createPortal(
    <div className="map-tip" popover="manual" ref={tipRef}>
      <MapTipBody card={card} activeSegNames={activeSegNames} />
    </div>,
    document.body,
  );
}
