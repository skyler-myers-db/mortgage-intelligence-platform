/**
 * The borrower-id morph (audit motion-03 phase 2, shell-10, stack-04,
 * runtime-10, css-10; D-shell-deviations-c2). deviation:borrower-id-morph
 *
 * One shared-element pair: a Lead Queue row's masked borrower id morphs into
 * the Borrower 360 title id across the route's View Transition. The prototype
 * has no route motion (design_files/Module 0 Prototype.html:1234-1236).
 *
 * A capture-phase document click listener names the SOURCE before React's
 * root listener runs the Link's navigation (and so before React calls
 * document.startViewTransition): only for a primary, unmodified click on a
 * same-origin link to /borrower-360/{masked id}, not target=_blank, whose row
 * id (`tr[data-borrower-row] .lead-table__borrower`) is on screen inside
 * `.main`, with View Transitions supported and motion not reduced. The
 * TARGET (routes/borrower-360.title.tsx) reads borrowerMorphNameFor(id) once,
 * at mount. Back / Forward and the pagers navigate programmatically, so they
 * never pass this listener and never morph. The mark clears after a second,
 * on the next location commit (AppShell) and when the title lets go, so a
 * stale or duplicate name can never abort a later transition. AppShell loads
 * this module as its own small chunk, outside the initial closure; the
 * title's release timing lives with the title.
 */
import { MASKED_BORROWER_ID_RE } from './routeMeta';

export const BORROWER_MORPH_NAME = 'mip-borrower-id';
const HOLD_MS = 1000;
const BORROWER_PATH_RE = /^\/borrower-360\/([^/]+)\/?$/;

let marked: HTMLElement | null = null;
let pending: { id: string; at: number } | null = null;
let expiry = 0;

/** Drop the source name and the pending pair. */
export function clearBorrowerMorph(): void {
  marked?.style.removeProperty('view-transition-name');
  marked = pending = null;
  window.clearTimeout(expiry);
}

/** The target's name: only for the id a click just marked, within the hold. */
export function borrowerMorphNameFor(id: string | undefined): string | undefined {
  return pending && pending.id === id && performance.now() - pending.at < HOLD_MS ? BORROWER_MORPH_NAME : undefined;
}

/** The masked id a same-origin, same-tab link to a dossier names, else null. */
function linkedBorrowerId(anchor: HTMLAnchorElement): string | null {
  const match = anchor.target !== '_blank' && anchor.origin === window.location.origin
    ? BORROWER_PATH_RE.exec(anchor.pathname)
    : null;
  try {
    const id = match ? decodeURIComponent(match[1]) : '';
    return MASKED_BORROWER_ID_RE.test(id) ? id : null;
  } catch {
    return null;
  }
}

/** A non-empty box that intersects .main's visible rect. */
function onScreen(element: Element): boolean {
  const rect = element.getBoundingClientRect();
  const main = document.querySelector('.main')?.getBoundingClientRect();
  return Boolean(main && rect.width && rect.height && rect.bottom > main.top && rect.top < main.bottom
    && rect.right > main.left && rect.left < main.right);
}

function onClick(event: MouseEvent): void {
  if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const anchor = event.target instanceof Element ? event.target.closest('a[href]') : null;
  const id = anchor instanceof HTMLAnchorElement ? linkedBorrowerId(anchor) : null;
  if (!id || !document.startViewTransition || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
  const source = document.querySelector(`tr[data-borrower-row="${CSS.escape(id)}"] .lead-table__borrower`);
  if (!(source instanceof HTMLElement) || !onScreen(source)) return;
  clearBorrowerMorph();
  source.style.setProperty('view-transition-name', BORROWER_MORPH_NAME);
  marked = source;
  pending = { id, at: performance.now() };
  expiry = window.setTimeout(clearBorrowerMorph, HOLD_MS);
}

/** Install the one capture-phase listener; returns its uninstaller. */
export function installBorrowerMorph(): () => void {
  document.addEventListener('click', onClick, true);
  return () => {
    document.removeEventListener('click', onClick, true);
    clearBorrowerMorph();
  };
}
