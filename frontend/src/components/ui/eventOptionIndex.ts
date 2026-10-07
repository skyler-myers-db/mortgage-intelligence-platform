import type { MouseEvent as ReactMouseEvent } from 'react';

/**
 * The option a delegated listbox mouse event landed on (audit a11y-05):
 * FilterSelect and MultiFilterSelect handle option clicks and hover on the
 * `role="listbox"` element, which owns the keyboard handler, instead of on
 * each `role="option"`, which never takes focus. Each option carries its
 * index in `data-option-index`; a press on its glyph or count still resolves
 * to it. Null when the event did not land inside an option of this listbox.
 */
export function eventOptionIndex(event: ReactMouseEvent<HTMLElement>): number | null {
  const target = event.target instanceof Element ? event.target.closest('[data-option-index]') : null;
  if (!target || !event.currentTarget.contains(target)) return null;
  const index = Number(target.getAttribute('data-option-index'));
  return Number.isInteger(index) && index >= 0 ? index : null;
}
