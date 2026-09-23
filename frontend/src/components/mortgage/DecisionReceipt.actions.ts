/**
 * The two receipt actions that touch browser APIs, kept out of the component
 * so the unit test can drive them against stubs.
 */

export const PRINT_MODE_ATTRIBUTE = 'print';
export const PRINT_MODE_VALUE = 'decision-receipt';
export const PRINT_HOST_CLASS = 'decision-receipt-print';

/** Copy the audit id to the clipboard; false when the browser refuses. */
export async function copyAuditId(auditEventId: string): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return false;
    await navigator.clipboard.writeText(auditEventId);
    return true;
  } catch {
    return false;
  }
}

const ID_REFERENCE_ATTRIBUTES = ['aria-labelledby', 'aria-describedby', 'aria-controls'] as const;

/**
 * A copy of the card for the print host. It sits beside the live card until
 * `afterprint`, so it drops every `id` (the `useId` title id) and the ARIA
 * references to them: the document never holds duplicate ids.
 */
function printableClone(card: HTMLElement): HTMLElement {
  const clone = card.cloneNode(true) as HTMLElement;
  for (const element of [clone, ...clone.querySelectorAll<HTMLElement>('*')]) {
    element.removeAttribute('id');
    for (const attribute of ID_REFERENCE_ATTRIBUTES) element.removeAttribute(attribute);
  }
  return clone;
}

/**
 * Print the receipt only: clone the card into a print host outside the app
 * root, flag `<html data-print="decision-receipt">` so the print sheet hides
 * `#root` and shows the host, print, then clean up after `afterprint`.
 * Returns false when there is nothing to print or the browser cannot print.
 */
export function printReceipt(card: HTMLElement | null): boolean {
  if (!card || typeof window === 'undefined' || typeof window.print !== 'function') return false;
  const root = document.documentElement;
  const host = document.createElement('div');
  host.className = PRINT_HOST_CLASS;
  host.appendChild(printableClone(card));
  document.body.appendChild(host);
  root.dataset[PRINT_MODE_ATTRIBUTE] = PRINT_MODE_VALUE;
  const cleanup = () => {
    window.removeEventListener('afterprint', cleanup);
    host.remove();
    delete root.dataset[PRINT_MODE_ATTRIBUTE];
  };
  window.addEventListener('afterprint', cleanup);
  try {
    window.print();
  } catch {
    cleanup();
    return false;
  }
  return true;
}
