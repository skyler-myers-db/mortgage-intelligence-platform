import { useEffect, useRef, type RefObject } from 'react';
import { pushEscapeLayer } from '../lib/escapeStack';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'textarea:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/** About half a second of frames: enough for a just-opened panel to become visible. */
const FOCUS_RETRY_FRAMES = 30;

interface UseFocusTrapOptions<TContainer extends HTMLElement, TInitial extends HTMLElement> {
  open: boolean;
  containerRef: RefObject<TContainer | null>;
  initialFocusRef?: RefObject<TInitial | null>;
  onClose: () => void;
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true',
  );
}

export function useFocusTrap<TContainer extends HTMLElement, TInitial extends HTMLElement = HTMLElement>({
  open,
  containerRef,
  initialFocusRef,
  onClose,
}: UseFocusTrapOptions<TContainer, TInitial>) {
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

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
      onCloseRef.current();
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
      if (lastFocused && typeof lastFocused.focus === 'function' && document.contains(lastFocused)) {
        lastFocused.focus();
      }
    };
  }, [containerRef, initialFocusRef, open]);
}
