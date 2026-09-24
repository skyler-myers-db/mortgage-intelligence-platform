import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type FocusEvent,
} from 'react';
import { Link, useLocation } from 'react-router';
import { AUDIT_EVENT_ID_PARAM, AUDIT_EXPLORER_PATH, auditEventHref } from '../../lib/auditLinks';
import { dismissToast, getToasts, subscribeToasts, type Toast } from '../../lib/toast';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import './Toaster.css';

/**
 * Toaster — the app shell's one transient-feedback region (2026-09-21 audit
 * states-07, slice 1). Renders what `lib/toast` holds.
 *
 * BEM block `.toast` (./Toaster.css, shipped with this lazy chunk) is a DECLARED
 * EXTENSION: the prototype (design_files/index.html) defines no transient-
 * feedback pattern, so it is built only from existing tokens and the
 * `.surface` / `.approval` vocabulary, not a toast library's look. Buttons
 * are plain `.btn` markup: the Button primitive's module (EvidenceChip, the
 * hover card) lives in a lazy chunk the shell must not pull in.
 *
 *   - The region is `popover="manual"`, shown once on mount, so toasts sit in
 *     the top layer above the Console, drawers and the Genie panel without a
 *     z-index race. Where the Popover API is missing the same element is a
 *     fixed-position region on `--z-toast`.
 *   - Success toasts render inside one persistent `role="status"` polite
 *     live region (it exists before any toast is inserted, so additions are
 *     announced). A failure toast carries `role="alert"` itself: an inserted
 *     alert is announced, and the shell never holds an empty alert region.
 *   - A toast with an `auditEventId` links to that ledger row; an actor who
 *     cannot open the admin audit explorer sees the id as text instead
 *     (lib/auditLinks). The toast goes once the explorer is showing its row,
 *     never on the click itself: the unsaved-changes guard can still hold
 *     that navigation, and Stay must find the toast and its link where they
 *     were.
 *   - Removing the toast that holds focus (a dismiss, or the cap evicting
 *     it) hands focus on first (WCAG 2.4.3):
 *     to the next toast's dismiss button, else back to the control focus
 *     came from before it entered the region, else to the page heading.
 *     A keyboard dismissal may scroll that control back into view; a mouse
 *     dismissal never scrolls the page (the person is looking at the toast,
 *     not at the control, which may be far off-screen by now).
 *   - Success toasts dismiss after `SUCCESS_TOAST_MS`; the timer pauses while
 *     the pointer is over the region or focus is inside it (WCAG 2.2.1).
 *     Failures stay until dismissed.
 */

export const SUCCESS_TOAST_MS = 8000;

function supportsPopover(element: HTMLElement): boolean {
  return typeof element.showPopover === 'function';
}

/**
 * Move focus off `card` before it is removed (a removed element drops focus
 * to <body>): the next toast's dismiss button (the previous toast's for the
 * last one), else `origin`, the element focus came from when it entered the
 * region, else the page `<h1>`, else `<main>`. The first that takes focus wins.
 *
 * `pointer` is a mouse (or touch) dismissal: every hand-off then keeps the
 * page where it is. Focusing `origin` without `preventScroll` scrolled a
 * control the person had long scrolled past (Share this build) back into
 * view, a page jump on every mouse close. A keyboard dismissal keeps the
 * scroll, so the control that now holds focus is on screen (WCAG 2.4.11).
 */
function focusAwayFrom(
  region: HTMLElement,
  card: HTMLElement,
  origin: HTMLElement | null,
  pointer: boolean,
): void {
  // Only a toast the store still holds can take focus: a burst can evict
  // more than one card in the frame before React re-renders the region.
  const live = new Set(getToasts().map((item) => String(item.id)));
  const cards = [...region.querySelectorAll<HTMLElement>('.toast')].filter(
    (item) => item === card || live.has(item.dataset.toastId ?? ''),
  );
  const index = cards.indexOf(card);
  const neighbour = index === -1 ? undefined : cards[index + 1] ?? cards[index - 1];
  const main = document.getElementById('main-content');
  const candidates: Array<[HTMLElement | null | undefined, boolean]> = [
    [neighbour?.querySelector<HTMLElement>('.toast__close'), pointer],
    [origin?.isConnected && !region.contains(origin) ? origin : null, pointer],
    // The heading and <main> are last resorts: take focus without scrolling the page.
    [main?.querySelector<HTMLElement>('h1[tabindex]'), true],
    [main, true],
  ];
  for (const [candidate, preventScroll] of candidates) {
    if (!candidate) continue;
    candidate.focus({ preventScroll });
    if (document.activeElement === candidate) return;
  }
}

/** Dismiss a success toast after its time on screen, paused while `paused`. */
function useAutoDismiss(toast: Toast, paused: boolean): void {
  const remainingRef = useRef(SUCCESS_TOAST_MS);

  // A coalesced repeat is news again: give it the full time.
  useEffect(() => {
    remainingRef.current = SUCCESS_TOAST_MS;
  }, [toast.revision]);

  useEffect(() => {
    if (toast.tone !== 'success' || paused) return undefined;
    const startedAt = performance.now();
    const timer = window.setTimeout(() => dismissToast(toast.id), remainingRef.current);
    return () => {
      window.clearTimeout(timer);
      remainingRef.current = Math.max(0, remainingRef.current - (performance.now() - startedAt));
    };
  }, [paused, toast.id, toast.revision, toast.tone]);
}

interface ToastCardProps {
  toast: Toast;
  paused: boolean;
  canOpenAudit: boolean;
  /** `clickDetail` is the click's `detail`: 0 for Enter / Space, 1+ for a pointer. */
  onDismiss: (id: number, clickDetail: number) => void;
}

function ToastCard({ toast, paused, canOpenAudit, onDismiss }: ToastCardProps) {
  useAutoDismiss(toast, paused);
  const failed = toast.tone === 'error';
  return (
    <div
      className={`toast toast--${toast.tone}`}
      role={failed ? 'alert' : undefined}
      data-toast-id={toast.id}
    >
      <Icon name={failed ? 'cross' : 'check'} size={16} className="toast__ico" />
      <div className="toast__body">
        <div className="toast__title">
          {toast.title}
          {toast.count > 1 && (
            <span className="chip chip--neutral chip--compact toast__count">
              <span aria-hidden="true">×{toast.count}</span>
              <span className="sr-only">{` (${toast.count} times)`}</span>
            </span>
          )}
        </div>
        {toast.detail && <div className="toast__detail">{toast.detail}</div>}
        {toast.auditEventId && (
          canOpenAudit ? (
            <Link className="toast__link" to={auditEventHref(toast.auditEventId)}>
              View audit event
            </Link>
          ) : (
            <div className="toast__detail">
              Audit event <span className="mono">{toast.auditEventId}</span>
            </div>
          )
        )}
      </div>
      <button
        type="button"
        className="btn btn--ghost btn--sm toast__close"
        aria-label="Dismiss notification"
        onClick={(event) => onDismiss(toast.id, event.detail)}
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  );
}

export function Toaster() {
  const toasts = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  const { canAccessAdmin } = useApp();
  const regionRef = useRef<HTMLElement | null>(null);
  /** Where focus was before it last entered the region (see focusAwayFrom). */
  const originRef = useRef<HTMLElement | null>(null);
  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const { pathname, search } = useLocation();

  // Promote the region to the top layer once; it stays open (and empty) for
  // the life of the shell, so the polite live region is always present.
  useLayoutEffect(() => {
    const region = regionRef.current;
    if (!region || !supportsPopover(region)) return undefined;
    if (!region.matches(':popover-open')) region.showPopover();
    return () => {
      if (region.matches(':popover-open')) region.hidePopover();
    };
  }, []);

  const onFocus = (event: FocusEvent<HTMLElement>) => {
    setFocusWithin(true);
    // Record the origin each time focus ENTERS the region; a move from one
    // toast to another keeps it.
    const from = event.relatedTarget;
    if (!(from instanceof Node) || !event.currentTarget.contains(from)) {
      originRef.current = from instanceof HTMLElement ? from : null;
    }
  };
  const onBlur = (event: FocusEvent<HTMLElement>) => {
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !event.currentTarget.contains(next)) setFocusWithin(false);
  };
  // Hover is tracked with React's pointer enter/leave, but a card removed from
  // under a resting pointer sends the region no pointerleave, so it would stay
  // paused and every later toast would never auto-dismiss (wave 1c review).
  // While hovered, re-derive it from where the pointer actually is.
  useEffect(() => {
    if (!hovered) return undefined;
    const onPointerOver = (event: PointerEvent) => {
      const region = regionRef.current;
      setHovered(Boolean(region && event.target instanceof Node && region.contains(event.target)));
    };
    document.addEventListener('pointerover', onPointerOver, true);
    return () => document.removeEventListener('pointerover', onPointerOver, true);
  }, [hovered]);

  const dismiss = useCallback((id: number, clickDetail = 0) => {
    // A pointer dismiss removes the card under the pointer: stop pausing now;
    // if another toast sits under it, the next pointer move pauses again.
    if (clickDetail > 0) setHovered(false);
    const region = regionRef.current;
    const card = region?.querySelector<HTMLElement>(`[data-toast-id="${id}"]`) ?? null;
    const active = document.activeElement;
    if (region && card && active && card.contains(active)) {
      focusAwayFrom(region, card, originRef.current, clickDetail > 0);
    }
    // A removed element fires no blur: unless focus now sits on another
    // toast, stop pausing, or every later toast would stay paused.
    const now = document.activeElement;
    if (!region || !now || card?.contains(now) || !region.contains(now)) setFocusWithin(false);
    dismissToast(id);
  }, []);

  // The store can also remove the toast that holds focus with no dismiss:
  // the cap evicts it for a newer one, or an actor change clears them all.
  // A store listener runs at publish, while that card is still in the DOM
  // (React re-renders the region after), so focus is handed on the same way.
  useEffect(() => subscribeToasts(() => {
    const region = regionRef.current;
    const active = document.activeElement;
    if (!region || !active || !region.contains(active)) return;
    const card = active.closest<HTMLElement>('.toast');
    if (!card || getToasts().some((item) => String(item.id) === card.dataset.toastId)) return;
    focusAwayFrom(region, card, originRef.current, false);
    // Focus nothing could take falls to <body> with no blur: stop pausing.
    if (card.contains(document.activeElement)) setFocusWithin(false);
  }), []);

  // A toast whose audit event the explorer now shows has done its job. It is
  // dismissed here, after the navigation committed, not in the link's click.
  useEffect(() => {
    if (pathname !== AUDIT_EXPLORER_PATH) return;
    const shown = new URLSearchParams(search).get(AUDIT_EVENT_ID_PARAM);
    if (!shown) return;
    for (const item of getToasts()) {
      if (item.auditEventId !== shown) continue;
      // Its link was likely clicked with the mouse, which rests over the card.
      setHovered(false);
      dismiss(item.id);
    }
  }, [dismiss, pathname, search]);

  const paused = hovered || focusWithin;
  const failures = toasts.filter((toast) => toast.tone === 'error');
  const confirmations = toasts.filter((toast) => toast.tone !== 'error');
  return (
    <section
      ref={regionRef}
      className="toast-region"
      popover="manual"
      aria-label="Notifications"
      data-paused={paused || undefined}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      onFocus={onFocus}
      onBlur={onBlur}
    >
      {failures.map((toast) => (
        <ToastCard key={toast.id} toast={toast} paused={paused} canOpenAudit={canAccessAdmin} onDismiss={dismiss} />
      ))}
      <div className="toast-region__list" role="status" aria-live="polite" aria-atomic="false">
        {confirmations.map((toast) => (
          <ToastCard key={toast.id} toast={toast} paused={paused} canOpenAudit={canAccessAdmin} onDismiss={dismiss} />
        ))}
      </div>
    </section>
  );
}
