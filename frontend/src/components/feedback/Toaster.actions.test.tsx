/**
 * @vitest-environment happy-dom
 *
 * The shell toast platform (W5b; states-07 item 1 and states-08 item 3
 * prerequisites, wave-3 review #13 and the 12.3 toast-in-modal leftover;
 * deviation:toast-actions-and-path) at the rendered layer: the info tone, the
 * one-shot action and its Retry-After countdown, F8, and the region inside a
 * modal: a click or a key on a toast control never reaches the dialog
 * portal's React ancestors, while Tab still wraps in the dialog's focus trap.
 */
import { act, useRef, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useModalDialog } from '../../hooks/useModalDialog';
import { listKeyBindings } from '../../lib/keymap';
import { clearToasts, getToasts, toast } from '../../lib/toast';
import { SUCCESS_TOAST_MS, TOAST_REGION_SHORTCUT, Toaster } from './Toaster';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const session = { canAccessAdmin: true };
vi.mock('../AppContext', () => ({ useApp: () => session }));
vi.mock('../../lib/sessionQuery', () => ({ useAuditLedgerAccess: () => false }));

const INFO_GLYPH = 'M12 8v.01M11 12h1v5h1';

const cards = () => [...document.querySelectorAll<HTMLElement>('.toast')];
const actionButton = (card: HTMLElement | undefined) => card?.querySelector<HTMLButtonElement>('.toast__action') ?? null;
const closeButton = (card: HTMLElement | undefined) => card?.querySelector<HTMLButtonElement>('.toast__close') ?? null;

function key(target: EventTarget, init: KeyboardEventInit): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

describe('Toaster: info, actions and F8', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-01T12:00:00Z'));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <MemoryRouter>
          <Toaster />
        </MemoryRouter>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    act(() => clearToasts());
    document.body.innerHTML = '';
    vi.useRealTimers();
  });

  it('an info toast sits in the polite status list with the info glyph and stays past the success time', () => {
    act(() => {
      toast.info('The signed-in user changed, so this tab was reset.');
    });
    const [card] = cards();
    expect(card.classList.contains('toast--info')).toBe(true);
    expect(card.closest('[role="status"][aria-live="polite"]')).not.toBeNull();
    expect(card.getAttribute('role')).toBeNull();
    expect(card.querySelector('.toast__ico')?.innerHTML).toContain(INFO_GLYPH);
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS * 5);
    });
    expect(cards()).toHaveLength(1);
    expect(closeButton(cards()[0]), 'it keeps its Close').not.toBeNull();
  });

  it('runs the action once, then dismisses it with the Close hand-off', () => {
    const share = document.createElement('button');
    share.textContent = 'Share this build';
    document.body.appendChild(share);
    const retry = vi.fn();
    act(() => {
      toast.error('Approve failed for 3 borrowers', { action: { label: 'Retry', onAction: retry } });
    });
    const button = actionButton(cards()[0]);
    expect(button?.textContent).toBe('Retry');
    const title = cards()[0].querySelector('.toast__title');
    expect(button?.getAttribute('aria-describedby')).toBe(title?.id);
    act(() => share.focus());
    act(() => button?.focus());
    // A double activation before the card goes: one run.
    act(() => {
      button?.click();
      button?.click();
    });
    expect(retry).toHaveBeenCalledOnce();
    expect(cards()).toHaveLength(0);
    expect(document.activeElement, 'focus goes back where it came from').toBe(share);
  });

  it('a throwing action still dismisses the toast; the error surfaces later', async () => {
    const thrown: unknown[] = [];
    const onError = (event: ErrorEvent) => {
      thrown.push(event.error);
      event.preventDefault();
    };
    window.addEventListener('error', onError);
    const original = globalThis.queueMicrotask;
    const queued: Array<() => void> = [];
    globalThis.queueMicrotask = (callback) => queued.push(callback);
    try {
      act(() => {
        toast.error('Assign failed', { action: { label: 'Retry', onAction: () => { throw new Error('consumer bug'); } } });
      });
      act(() => actionButton(cards()[0])?.click());
      expect(cards()).toHaveLength(0);
      expect(queued).toHaveLength(1);
      expect(() => queued[0]()).toThrow('consumer bug');
    } finally {
      globalThis.queueMicrotask = original;
      window.removeEventListener('error', onError);
    }
  });

  it('holds the action aria-disabled behind a countdown while a Retry-After runs, then makes it live', () => {
    const retry = vi.fn();
    act(() => {
      toast.error('Too many requests', { action: { label: 'Retry', onAction: retry }, retryAfterMs: 3_000 });
    });
    const button = () => actionButton(cards()[0]);
    expect(button()?.getAttribute('aria-disabled')).toBe('true');
    expect(button()?.hasAttribute('disabled'), 'never natively disabled').toBe(false);
    expect(cards()[0].querySelector('.toast__wait')?.textContent).toContain('Try again in');
    expect(cards()[0].querySelector('.toast__wait')?.textContent).toContain('3 s');
    act(() => button()?.click());
    expect(retry, 'a click while it waits does nothing').not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(button()?.hasAttribute('aria-disabled')).toBe(false);
    expect(cards()[0].querySelector('.toast__wait')).toBeNull();
    act(() => button()?.click());
    expect(retry).toHaveBeenCalledOnce();
  });

  it('a success toast with an action keeps its timer', () => {
    act(() => {
      toast.success('Assigned 12 borrowers', { action: { label: 'Undo', onAction: () => undefined } });
    });
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS);
    });
    expect(cards()).toHaveLength(0);
  });

  it('two action toasts with identical text stay two', () => {
    const retry = vi.fn();
    act(() => {
      toast.error('Approve failed', { action: { label: 'Retry', onAction: retry } });
      toast.error('Approve failed', { action: { label: 'Retry', onAction: retry } });
    });
    expect(cards()).toHaveLength(2);
    expect(cards().some((card) => card.querySelector('.toast__count'))).toBe(false);
  });

  it('F8 focuses the newest toast (its live action, else its Close) from anywhere, and declines with none', () => {
    expect(listKeyBindings().find((binding) => binding.id === 'toast-region')?.description).toBe(TOAST_REGION_SHORTCUT);
    const field = document.createElement('input');
    document.body.appendChild(field);
    act(() => field.focus());
    expect(key(field, { key: 'F8' }).defaultPrevented, 'no toast: F8 falls through').toBe(false);
    expect(document.activeElement).toBe(field);

    act(() => {
      toast.success('Build saved');
      toast.error('Too many requests', { action: { label: 'Retry', onAction: () => undefined }, retryAfterMs: 5_000 });
    });
    expect(key(field, { key: 'F8' }).defaultPrevented).toBe(true);
    const newest = cards().find((card) => card.textContent?.includes('Too many requests'));
    expect(document.activeElement, 'the held action is skipped: Close').toBe(closeButton(newest));

    act(() => {
      toast.info('Assignment recorded', { action: { label: 'View receipt', onAction: () => undefined } });
    });
    key(document.body, { key: 'F8' });
    expect(document.activeElement?.textContent).toBe('View receipt');
  });
});

/** A parent with React handlers whose modal renders through a body portal, as BorrowerProofDrawer does. */
function ModalHost({
  onParentClick,
  onParentKeyDown,
  onDismiss,
  closeAt,
}: {
  onParentClick: (event: ReactMouseEvent) => void;
  onParentKeyDown: (event: ReactKeyboardEvent) => void;
  onDismiss: () => void;
  /** Where the dialog's own control renders ('none' until a re-render adds it). */
  closeAt: 'first' | 'last' | 'none';
}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  useModalDialog({ open: true, dialogRef, onDismiss, backdrop: false });
  return (
    <div onClick={onParentClick} onKeyDown={onParentKeyDown} role="presentation">
      {createPortal(
        <dialog ref={dialogRef} aria-label="Borrower proof" tabIndex={-1}>
          {closeAt === 'first' && <button type="button" id="dialog-close">Close drawer</button>}
          <p>Proof</p>
          {closeAt === 'last' && <button type="button" id="dialog-close">Close drawer</button>}
        </dialog>,
        document.body,
      )}
    </div>
  );
}

describe('Toaster inside a modal dialog', () => {
  let root: Root;
  let container: HTMLElement;
  let parentClick: ReturnType<typeof vi.fn<(event: ReactMouseEvent) => void>>;
  let parentKeyDown: ReturnType<typeof vi.fn<(event: ReactKeyboardEvent) => void>>;
  let dismissed: ReturnType<typeof vi.fn<() => void>>;

  beforeEach(() => {
    vi.useFakeTimers();
    parentClick = vi.fn<(event: ReactMouseEvent) => void>();
    parentKeyDown = vi.fn<(event: ReactKeyboardEvent) => void>();
    dismissed = vi.fn<() => void>();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    act(() => clearToasts());
    document.body.innerHTML = '';
    vi.useRealTimers();
  });

  /** The toast region mounts first, then the modal opens over it. */
  function openModal({ closeLast = false }: { closeLast?: boolean } = {}): HTMLDialogElement {
    const render = (modal: boolean, closeAt: 'first' | 'last' | 'none') => (
      <MemoryRouter>
        <Toaster />
        {modal && (
          <ModalHost onParentClick={parentClick} onParentKeyDown={parentKeyDown} onDismiss={dismissed} closeAt={closeAt} />
        )}
      </MemoryRouter>
    );
    act(() => root.render(render(false, 'none')));
    act(() => root.render(render(true, closeLast ? 'none' : 'first')));
    // Rendered once the region has moved in, React appends the control after
    // the region's container: the toasts come first in the dialog.
    if (closeLast) act(() => root.render(render(true, 'last')));
    const dialog = document.querySelector('dialog');
    if (!dialog) throw new Error('modal not rendered');
    expect(dialog.contains(document.querySelector('section.toast-region')), 'the region is re-hosted in the modal').toBe(true);
    return dialog;
  }

  function raiseTwoActionToasts(retry = vi.fn()): ReturnType<typeof vi.fn> {
    act(() => {
      toast.error('Approve failed', { action: { label: 'Retry', onAction: retry } });
      toast.error('Reject failed', { action: { label: 'Retry reject', onAction: () => undefined } });
    });
    return retry;
  }

  it("a click or Enter on a toast control never reaches the dialog's React ancestors; the toast's own action runs", () => {
    openModal();
    const retry = raiseTwoActionToasts();
    const button = actionButton(cards()[0]);
    act(() => button?.focus());
    key(button as HTMLButtonElement, { key: 'Enter' });
    expect(parentKeyDown, 'Enter on a toast control').not.toHaveBeenCalled();
    act(() => button?.click());
    expect(retry).toHaveBeenCalledOnce();
    expect(parentClick, 'a click on a toast control').not.toHaveBeenCalled();

    // Control: the dialog's own controls still reach their React ancestors.
    act(() => document.getElementById('dialog-close')?.click());
    expect(parentClick).toHaveBeenCalledOnce();
  });

  it('Escape on a focused toast control still dismisses the top layer', () => {
    openModal();
    raiseTwoActionToasts();
    const close = closeButton(cards()[0]);
    act(() => close?.focus());
    key(close as HTMLButtonElement, { key: 'Escape' });
    expect(dismissed).toHaveBeenCalledOnce();
  });

  it("Tab from the last toast control wraps to the dialog's first control (the focus trap still hears Tab)", () => {
    openModal();
    raiseTwoActionToasts();
    const last = closeButton(cards()[1]);
    act(() => last?.focus());
    expect(document.activeElement).toBe(last);
    const event = key(last as HTMLButtonElement, { key: 'Tab' });
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement?.id).toBe('dialog-close');
  });

  it("Shift+Tab from the first toast control wraps back to the dialog's last control", () => {
    openModal({ closeLast: true });
    raiseTwoActionToasts();
    const first = actionButton(cards()[0]);
    act(() => first?.focus());
    const event = key(first as HTMLButtonElement, { key: 'Tab', shiftKey: true });
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement?.id).toBe('dialog-close');
  });

  it('F8 pressed on a dialog control focuses the newest toast inside the modal', () => {
    const dialog = openModal();
    raiseTwoActionToasts();
    const own = document.getElementById('dialog-close') as HTMLButtonElement;
    act(() => own.focus());
    key(own, { key: 'F8' });
    expect(document.activeElement?.textContent).toBe('Retry reject');
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it('with no modal nothing is stopped: a toast click bubbles to the document', () => {
    act(() =>
      root.render(
        <MemoryRouter>
          <Toaster />
        </MemoryRouter>,
      ),
    );
    const seen = vi.fn();
    document.addEventListener('click', seen);
    try {
      act(() => {
        toast.error('Copy failed', { action: { label: 'Retry', onAction: () => undefined } });
      });
      act(() => actionButton(cards()[0])?.click());
      expect(seen).toHaveBeenCalledOnce();
      expect(getToasts()).toEqual([]);
    } finally {
      document.removeEventListener('click', seen);
    }
  });
});
