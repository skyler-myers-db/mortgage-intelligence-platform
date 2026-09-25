import { useEffect, useEffectEvent, type RefObject } from 'react';
import { pushEscapeLayer } from '../lib/escapeStack';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'textarea:not([disabled])',
  'select:not([disabled])',
  'summary',
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/** About half a second of frames: enough for a just-opened panel to become visible. */
const FOCUS_RETRY_FRAMES = 30;

interface UseFocusTrapOptions<TContainer extends HTMLElement, TInitial extends HTMLElement> {
  open: boolean;
  containerRef: RefObject<TContainer | null>;
  initialFocusRef?: RefObject<TInitial | null>;
  onClose: () => void;
  /**
   * Give focus back to the element that held it when the trap opened
   * (default). `useModalDialog` passes false: it restores focus itself,
   * after `dialog.close()`, because an opener behind an open modal is inert
   * and refuses focus.
   */
  restoreFocus?: boolean;
}

/**
 * Tab stops the wrap-around cycles through (audit a11y-07). The selector
 * alone also matched elements Tab skips: a control inside an `inert`
 * subtree (the retained copy of a closing panel, a closing filter menu) or
 * one that is not rendered (`display: none`, a closed `<details>` body).
 * Wrapping onto one of those left focus where it was, so Tab looked dead.
 * `checkVisibility` is missing in DOM test environments; there the check is
 * skipped.
 */
function isTabStop(element: HTMLElement): boolean {
  if (element.hasAttribute('disabled') || element.getAttribute('aria-hidden') === 'true') return false;
  if (element.closest('[inert]') !== null) return false;
  return typeof element.checkVisibility !== 'function' || element.checkVisibility();
}

export function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(isTabStop);
}

export function useFocusTrap<TContainer extends HTMLElement, TInitial extends HTMLElement = HTMLElement>({
  open,
  containerRef,
  initialFocusRef,
  onClose,
  restoreFocus = true,
}: UseFocusTrapOptions<TContainer, TInitial>) {
  // The latest onClose, read when Escape fires; not a dependency of the trap
  // (stack-04 tail), so a caller's inline handler never re-runs the trap and
  // never re-focuses the initial target.
  const onCloseEvent = useEffectEvent(onClose);

  useEffect(() => {
    if (!open) return undefined;

    const lastFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    let disposed = false;
    let retryFrame = 0;

    queueMicrotask(() => {
      const initialTarget = initialFocusRef?.current ?? containerRef.current;
      initialTarget?.focus();
      if (!initialTarget || document.activeElement === initialTarget) return;
      if (containerRef.current?.contains(document.activeElement)) return;
      // An always-mounted panel that has just opened (the evidence drawer)
      // can refuse focus for a few frames: under prefers-reduced-motion
      // every element transitions `all` for 0.01ms, so a descendant's
      // inherited `visibility` stays `hidden` until frames commit that
      // transition. Try again on each next frame (bounded), but never once
      // focus has moved elsewhere or the trap has closed.
      const missed = document.activeElement;
      const retry = (attempts: number) => {
        retryFrame = requestAnimationFrame(() => {
          retryFrame = 0;
          if (disposed || document.activeElement !== missed) return;
          const target = initialFocusRef?.current ?? containerRef.current;
          target?.focus();
          if (target && document.activeElement !== target && attempts > 1) retry(attempts - 1);
        });
      };
      retry(FOCUS_RETRY_FRAMES);
    });

    // Escape goes through the shared topmost-layer stack (audit 2026-09-21
    // runtime-v2): a trap opened above another overlay closes ALONE, instead
    // of every open layer reacting to the same keypress.
    const popEscapeLayer = pushEscapeLayer(() => {
      onCloseEvent();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;

      const container = containerRef.current;
      if (!container) return;

      const focusables = focusableElements(container);
      if (focusables.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;

      if (!active || !container.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      disposed = true;
      if (retryFrame) cancelAnimationFrame(retryFrame);
      popEscapeLayer();
      window.removeEventListener('keydown', onKeyDown);
      if (!restoreFocus) return;
      if (lastFocused && typeof lastFocused.focus === 'function' && document.contains(lastFocused)) {
        lastFocused.focus();
      }
    };
  }, [containerRef, initialFocusRef, open, restoreFocus]);
}
