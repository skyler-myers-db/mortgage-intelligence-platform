import { useEffect, type RefObject } from 'react';

/**
 * Keep the decision bar's buttons clear of the floating Genie panel (review of
 * the 2026-09-21 audit visual-v1 fix). The docked panel (`.genie`, fixed at
 * the bottom right, 06-genie-chat.css) sits over the right end of the docked
 * bar, where the gate's Reject and Approve buttons are, so with Genie open a
 * reviewer could neither see nor safely tab to them. While the panel is open
 * and docked, the bar reserves the panel's footprint as inline-end padding
 * (offer-orchestrator.action-bar.css), so its controls reflow to the left of
 * the panel.
 *
 * The panel's size is the user's (drag-resizable), so the footprint is
 * measured, not assumed. A dragged (undocked) panel is placed by the user and
 * left alone, as the Console offset rule does; a panel covering most of the
 * bar (a narrow viewport) is a sheet no padding can clear.
 */

/** Read by offer-orchestrator.action-bar.css: how far the panel reaches into the bar, in px. */
export const GENIE_CLEARANCE_PROPERTY = '--offer-action-bar-genie-clearance';
/** Set on the bar while the clearance applies (the CSS keys the padding off it). */
export const GENIE_CLEARANCE_ATTRIBUTE = 'data-genie-clearance';

/** A panel reaching further into the bar than this share is a sheet: reserve nothing. */
const MAX_CLEARANCE_SHARE = 0.6;

export interface InlineSpan {
  left: number;
  right: number;
}

/**
 * How far the panel reaches into the bar from its inline end, in px, or 0
 * when it does not overlap the bar or covers too much of it to clear.
 */
export function genieClearance(bar: InlineSpan, panel: InlineSpan): number {
  if (panel.left >= bar.right || panel.right <= bar.left) return 0;
  const reach = Math.ceil(bar.right - panel.left);
  const width = bar.right - bar.left;
  if (width <= 0 || reach > width * MAX_CLEARANCE_SHARE) return 0;
  return reach;
}

/** The panel's layout box. offsetLeft ignores the open/close transform (scale) mid-animation. */
function panelSpan(panel: HTMLElement): InlineSpan {
  return { left: panel.offsetLeft, right: panel.offsetLeft + panel.offsetWidth };
}

export function useGenieClearance(barRef: RefObject<HTMLElement | null>, genieOpen: boolean): void {
  useEffect(() => {
    const bar = barRef.current;
    if (!bar || !genieOpen) return undefined;

    let panel: HTMLElement | null = null;
    const release = () => {
      bar.removeAttribute(GENIE_CLEARANCE_ATTRIBUTE);
      bar.style.removeProperty(GENIE_CLEARANCE_PROPERTY);
    };
    const write = () => {
      if (!panel) return;
      if (!panel.classList.contains('is-open') || panel.classList.contains('is-undocked')) {
        release();
        return;
      }
      const rect = bar.getBoundingClientRect();
      const reach = genieClearance({ left: rect.left, right: rect.right }, panelSpan(panel));
      if (reach === 0) {
        release();
        return;
      }
      bar.style.setProperty(GENIE_CLEARANCE_PROPERTY, `${reach}px`);
      bar.setAttribute(GENIE_CLEARANCE_ATTRIBUTE, '');
    };

    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(write) : null;
    // Open / close / drag toggle the panel's classes; a resize rewrites its inline size.
    const panelChanges = new MutationObserver(write);
    // The chat mounts lazily on the first open, so the panel may not exist yet.
    const mount = new MutationObserver(() => attach());
    const attach = () => {
      panel = document.querySelector<HTMLElement>('.genie');
      if (!panel) return;
      mount.disconnect();
      resize?.observe(panel);
      panelChanges.observe(panel, { attributes: true, attributeFilter: ['class', 'style'] });
      write();
    };

    // The bar's own box moves with the Console, the window and the page.
    resize?.observe(bar);
    attach();
    if (!panel) mount.observe(document.body, { childList: true, subtree: true });

    return () => {
      mount.disconnect();
      panelChanges.disconnect();
      resize?.disconnect();
      release();
    };
  }, [barRef, genieOpen]);
}
