import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'textarea:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

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

    queueMicrotask(() => {
      const initialTarget = initialFocusRef?.current ?? containerRef.current;
      initialTarget?.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }

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
      window.removeEventListener('keydown', onKeyDown);
      if (lastFocused && typeof lastFocused.focus === 'function' && document.contains(lastFocused)) {
        lastFocused.focus();
      }
    };
  }, [containerRef, initialFocusRef, open]);
}
