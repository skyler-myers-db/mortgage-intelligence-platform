import { useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react';

/**
 * Viewport-edge flip for the anchored `.filter-menu` popups (2026-09-21 audit
 * stack-05 / tables-06). The menu is positioned by CSS below its trigger; a
 * filter near the bottom of the viewport used to open off-screen. On open we
 * measure once with getBoundingClientRect (no positioning library) and open
 * upward when the menu does not fit below and there is more room above.
 *
 * "Room" is measured inside the visible area the menu can paint in: the
 * viewport intersected with every clipping ancestor (the app scrolls inside
 * `.main`, whose top edge sits under the route nav, so the viewport alone
 * would flip a menu up into a region `.main` clips). The chosen side's room
 * also caps the menu height (`--filter-menu-space`), so a menu that fits
 * neither side scrolls inside the visible area instead of being cut off.
 */

export type MenuPlacement = 'below' | 'above';

export interface MenuLayout {
  placement: MenuPlacement;
  /** Pixels available on the chosen side, or null before the first measure. */
  space: number | null;
}

interface VerticalBounds {
  top: number;
  bottom: number;
}

const UNMEASURED: MenuLayout = { placement: 'below', space: null };

/** Trigger-to-menu gap: `--sp-1` in tokens.css. */
const MENU_GAP_PX = 4;

export function menuPlacement(
  anchor: VerticalBounds,
  menuHeight: number,
  bounds: VerticalBounds,
): Required<MenuLayout> {
  const spaceBelow = bounds.bottom - anchor.bottom - MENU_GAP_PX;
  const spaceAbove = anchor.top - bounds.top - MENU_GAP_PX;
  const placement: MenuPlacement = menuHeight > spaceBelow && spaceAbove > spaceBelow ? 'above' : 'below';
  return { placement, space: Math.max(0, Math.floor(placement === 'above' ? spaceAbove : spaceBelow)) };
}

/**
 * The viewport band the element can paint in: the viewport intersected with
 * every clipping ancestor, minus a sticky header pinned to the top of that
 * ancestor (the route nav sticks to the top of `.main` and paints over
 * whatever scrolls under it).
 */
export function visibleBounds(element: HTMLElement): VerticalBounds {
  let top = 0;
  let bottom = window.innerHeight;
  for (let node = element.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (!overflowY || overflowY === 'visible') continue;
    const rect = node.getBoundingClientRect();
    top = Math.max(top, rect.top);
    bottom = Math.min(bottom, rect.bottom);
    for (const child of node.children) {
      if (child.contains(element) || getComputedStyle(child).position !== 'sticky') continue;
      const header = child.getBoundingClientRect();
      if (header.top <= rect.top + 1) top = Math.max(top, header.bottom);
    }
  }
  return { top, bottom };
}

/** Inline custom property that caps the menu at the measured room. */
export function menuSpaceStyle(layout: MenuLayout): CSSProperties | undefined {
  return layout.space === null ? undefined : ({ '--filter-menu-space': `${layout.space}px` } as CSSProperties);
}

/**
 * Placement for a menu rendered while `open`. Measured in a layout effect,
 * so the flipped menu is what first paints.
 */
export function useMenuPlacement(
  open: boolean,
  anchorRef: RefObject<HTMLElement | null>,
  menuRef: RefObject<HTMLElement | null>,
): MenuLayout {
  const [layout, setLayout] = useState<MenuLayout>(UNMEASURED);
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const menu = menuRef.current;
    if (!open) {
      // Drop the cap, so the next open measures the menu's uncapped height.
      setLayout((current) => (current === UNMEASURED ? current : UNMEASURED));
      return;
    }
    if (!anchor || !menu) return;
    setLayout(menuPlacement(anchor.getBoundingClientRect(), menu.offsetHeight, visibleBounds(menu)));
  }, [anchorRef, menuRef, open]);
  return layout;
}
