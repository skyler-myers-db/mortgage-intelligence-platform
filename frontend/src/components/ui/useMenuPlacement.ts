import { useLayoutEffect, useState, type RefObject } from 'react';

/**
 * Viewport-edge flip for the anchored `.filter-menu` popups (2026-09-21 audit
 * stack-05 / tables-06). The menu is positioned by CSS below its trigger; a
 * filter near the bottom of the viewport used to open off-screen. On open we
 * measure once with getBoundingClientRect (no positioning library) and open
 * upward when the menu does not fit below and there is more room above.
 */

export type MenuPlacement = 'below' | 'above';

/** Trigger-to-menu gap: `--sp-1` in tokens.css. */
const MENU_GAP_PX = 4;

export function menuPlacement(
  anchor: Pick<DOMRect, 'top' | 'bottom'>,
  menuHeight: number,
  viewportHeight: number,
): MenuPlacement {
  const spaceBelow = viewportHeight - anchor.bottom - MENU_GAP_PX;
  const spaceAbove = anchor.top - MENU_GAP_PX;
  return menuHeight > spaceBelow && spaceAbove > spaceBelow ? 'above' : 'below';
}

/**
 * Placement for a menu rendered while `open`. Measured in a layout effect,
 * so the flipped menu is what first paints.
 */
export function useMenuPlacement(
  open: boolean,
  anchorRef: RefObject<HTMLElement | null>,
  menuRef: RefObject<HTMLElement | null>,
): MenuPlacement {
  const [placement, setPlacement] = useState<MenuPlacement>('below');
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const menu = menuRef.current;
    if (!open || !anchor || !menu) return;
    setPlacement(menuPlacement(anchor.getBoundingClientRect(), menu.offsetHeight, window.innerHeight));
  }, [anchorRef, menuRef, open]);
  return placement;
}
