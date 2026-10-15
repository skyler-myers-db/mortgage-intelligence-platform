/**
 * The text a ui/Tooltip trigger carries (critic-08: the design-system tooltip
 * replaced native `title`): its hidden description's text slot, without the
 * shortcut suffix. Null when the element is not a tooltip trigger, or under a
 * server render (react-dom/server renders no portal).
 */
export function tooltipText(element: Element | null | undefined): string | null {
  const id = element?.getAttribute('data-tooltip');
  if (!id) return null;
  return document.getElementById(id)?.querySelector('[data-tooltip-text]')?.textContent ?? null;
}
