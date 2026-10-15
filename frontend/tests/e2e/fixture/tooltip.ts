import type { Locator } from '@playwright/test';

/** The id of the design-system tooltip popup (ui/tooltipController.ts TOOLTIP_POPUP_ID). */
export const TOOLTIP_POPUP = '#mip-tooltip';

/**
 * The text a ui/Tooltip trigger carries (critic-08: the design-system tooltip
 * replaced native `title`): its hidden description's text slot, without the
 * shortcut suffix. Null when the element is not a tooltip trigger.
 */
export async function tooltipText(trigger: Locator): Promise<string | null> {
  return trigger.evaluate((node) => {
    const id = node.getAttribute('data-tooltip');
    if (!id) return null;
    return document.getElementById(id)?.querySelector('[data-tooltip-text]')?.textContent ?? null;
  });
}
