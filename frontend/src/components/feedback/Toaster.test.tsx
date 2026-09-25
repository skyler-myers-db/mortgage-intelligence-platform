/**
 * @vitest-environment happy-dom
 *
 * The shell toast region (audit states-07 slice 1) at the component grain:
 * live-region roles, the audit-event link, dismiss, auto-dismiss with
 * pause-on-hover / pause-on-focus, sticky failures and the coalesced count.
 * happy-dom has no Popover API, so this also covers the fixed-position
 * fallback path; feedback-guard.fixture.spec.ts proves the top-layer path.
 * The region renders through a portal container at the end of <body> (it
 * moves into an open modal dialog), so the queries are document-wide.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pushModalLayer } from '../../lib/modalLayers';
import { clearToasts, getToasts, toast } from '../../lib/toast';
import { SUCCESS_TOAST_MS, Toaster } from './Toaster';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const session = { canAccessAdmin: true };
vi.mock('../AppContext', () => ({ useApp: () => session }));

describe('Toaster', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    vi.useFakeTimers();
    session.canAccessAdmin = true;
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
    vi.useRealTimers();
  });

  const region = () => document.querySelector('section.toast-region');
  const cards = () => [...document.querySelectorAll<HTMLElement>('.toast')];

  it('is one labelled region with a persistent polite status list, empty at rest', () => {
    expect(region()?.getAttribute('aria-label')).toBe('Notifications');
    expect(region()?.getAttribute('popover')).toBe('manual');
    const list = region()?.querySelector('.toast-region__list');
    expect(list?.getAttribute('role')).toBe('status');
    expect(list?.getAttribute('aria-live')).toBe('polite');
    expect(list?.childElementCount).toBe(0);
    expect(document.querySelector('[role="alert"]')).toBeNull();
  });

  it('puts a confirmation in the status list and a failure in its own alert', () => {
    act(() => {
      toast.success('Build saved', { detail: 'IL refi cohort' });
      toast.error('Copy failed');
    });
    const list = region()?.querySelector('[role="status"]');
    expect(list?.textContent).toContain('Build saved');
    expect(list?.textContent).toContain('IL refi cohort');
    const alert = document.querySelector('.toast--error');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Copy failed');
    expect(list?.contains(alert as Node)).toBe(false);
  });

  it('links the audit event for an actor who can open the explorer, and shows the id otherwise', () => {
    act(() => {
      toast.success('Build saved', { auditEventId: 'evt-0001' });
    });
    const link = document.querySelector<HTMLAnchorElement>('a.toast__link');
    expect(link?.textContent).toBe('View audit event');
    expect(link?.getAttribute('href')).toBe('/admin-config?audit_event_id=evt-0001#audit');

    act(() => clearToasts());
    session.canAccessAdmin = false;
    act(() => {
      root.render(
        <MemoryRouter>
          <Toaster />
        </MemoryRouter>,
      );
      toast.success('Build saved', { auditEventId: 'evt-0002' });
    });
    expect(document.querySelector('a.toast__link')).toBeNull();
    expect(cards()[0].textContent).toContain('Audit event evt-0002');
  });

  it('dismisses a confirmation after its time on screen, and on the dismiss button', () => {
    act(() => {
      toast.success('Build link copied');
    });
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS - 1);
    });
    expect(cards()).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(cards()).toHaveLength(0);

    act(() => {
      toast.success('Build saved');
    });
    const dismiss = document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
    act(() => dismiss?.click());
    expect(getToasts()).toEqual([]);
  });

  it('holds every toast while the pointer is over the region or focus is inside it', () => {
    act(() => {
      toast.success('Build saved');
    });
    const section = region() as HTMLElement;
    act(() => {
      section.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
    });
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS * 3);
    });
    expect(cards()).toHaveLength(1);
    act(() => {
      section.dispatchEvent(new PointerEvent('pointerout', { bubbles: true, relatedTarget: document.body }));
    });
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS);
    });
    expect(cards()).toHaveLength(0);

    act(() => {
      toast.success('Build saved again');
    });
    const close = document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
    act(() => close?.focus());
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS * 3);
    });
    expect(cards()).toHaveLength(1);
    act(() => close?.blur());
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS);
    });
    expect(cards()).toHaveLength(0);
  });

  it('moves focus off a toast before removing it: next toast, then where focus came from, then the heading', () => {
    const main = document.createElement('main');
    main.id = 'main-content';
    main.tabIndex = -1;
    main.innerHTML = '<h1 tabindex="-1">Portfolio Builder</h1>';
    const share = document.createElement('button');
    share.textContent = 'Share this build';
    main.appendChild(share);
    document.body.appendChild(main);
    try {
      act(() => {
        toast.error('Copy failed');
        toast.error('Save failed');
      });
      const closes = () => [...document.querySelectorAll<HTMLButtonElement>('button[aria-label="Dismiss notification"]')];
      act(() => share.focus());
      act(() => closes()[0].focus());
      expect(document.activeElement).toBe(closes()[0]);

      // Two toasts: the next one's dismiss button takes focus.
      act(() => closes()[0].click());
      expect(cards().map((card) => card.textContent)).toEqual([expect.stringContaining('Save failed')]);
      expect(document.activeElement).toBe(closes()[0]);

      // The last toast: focus goes back to where it came from.
      act(() => closes()[0].click());
      expect(cards()).toHaveLength(0);
      expect(document.activeElement).toBe(share);

      // Where focus came from is gone: the page heading takes it.
      act(() => {
        toast.error('Copy failed');
      });
      act(() => closes()[0].focus());
      act(() => share.remove());
      act(() => closes()[0].click());
      expect(document.activeElement).toBe(main.querySelector('h1'));
    } finally {
      main.remove();
    }
  });

  it('hands focus back without scrolling on a mouse close, and with it on a keyboard close', () => {
    const share = document.createElement('button');
    share.textContent = 'Share this build';
    document.body.appendChild(share);
    const focusShare = vi.spyOn(share, 'focus');
    const close = () => document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
    try {
      // Enter / Space on the dismiss button is a click with detail 0.
      act(() => {
        toast.success('Build link copied');
      });
      act(() => share.focus());
      act(() => close()?.focus());
      act(() => close()?.click());
      expect(cards()).toHaveLength(0);
      expect(document.activeElement).toBe(share);
      expect(focusShare).toHaveBeenLastCalledWith({ preventScroll: false });

      // A mouse click carries its click count in detail: keep the page still.
      act(() => {
        toast.success('Build link copied');
      });
      act(() => close()?.focus());
      act(() => {
        close()?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
      });
      expect(cards()).toHaveLength(0);
      expect(document.activeElement).toBe(share);
      expect(focusShare).toHaveBeenLastCalledWith({ preventScroll: true });
    } finally {
      share.remove();
    }
  });

  it('hands focus on when the cap evicts the toast that holds it', () => {
    act(() => {
      toast.success('Build link copied');
    });
    const focused = document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
    act(() => focused?.focus());
    expect(document.activeElement).toBe(focused);

    // Three failures, each raised by its own event: the third evicts the
    // oldest confirmation, the focused one.
    for (const title of ['Copy failed', 'Save failed', 'Export failed']) {
      act(() => {
        toast.error(title);
      });
    }
    expect(cards().map((card) => card.textContent)).toEqual([
      expect.stringContaining('Copy failed'),
      expect.stringContaining('Save failed'),
      expect.stringContaining('Export failed'),
    ]);
    const saveFailed = cards()[1].querySelector('button[aria-label="Dismiss notification"]');
    expect(document.activeElement).toBe(saveFailed);
  });

  it('never hands focus to a toast the same burst also evicted', () => {
    const share = document.createElement('button');
    share.textContent = 'Share this build';
    document.body.appendChild(share);
    try {
      act(() => {
        toast.success('Build link copied');
        toast.success('Build saved');
      });
      act(() => share.focus());
      act(() => document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]')?.focus());

      // One synchronous burst, before React re-renders the region: the
      // second failure evicts the focused toast (focus moves to its
      // neighbour), the third evicts that neighbour too.
      act(() => {
        toast.error('Copy failed');
        toast.error('Save failed');
        toast.error('Export failed');
      });
      expect(cards()).toHaveLength(3);
      expect(document.activeElement).toBe(share);
    } finally {
      share.remove();
    }
  });

  it('hands focus to the heading when an actor change clears the focused toast, and stops pausing', () => {
    const closeButton = () => document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
    const main = document.createElement('main');
    main.id = 'main-content';
    main.tabIndex = -1;
    main.innerHTML = '<h1 tabindex="-1">Lead Queue</h1>';
    document.body.appendChild(main);
    try {
      act(() => {
        toast.error('Copy failed');
      });
      act(() => closeButton()?.focus());
      act(() => clearToasts());
      expect(document.activeElement).toBe(main.querySelector('h1'));
    } finally {
      main.remove();
    }

    // Nothing left to take focus: it falls to <body>, and the region must
    // not stay paused, or the next confirmation would never time out.
    act(() => {
      toast.error('Copy failed');
    });
    act(() => closeButton()?.focus());
    act(() => clearToasts());
    act(() => {
      toast.success('Build saved');
    });
    expect(region()?.hasAttribute('data-paused')).toBe(false);
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS);
    });
    expect(cards()).toHaveLength(0);
  });

  it('keeps a failure until it is dismissed', () => {
    act(() => {
      toast.error('Copy failed');
    });
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS * 10);
    });
    expect(cards()).toHaveLength(1);
  });

  it('shows a repeat as one toast with a count, and restarts its time on screen', () => {
    act(() => {
      toast.success('Build link copied');
    });
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS - 100);
    });
    act(() => {
      toast.success('Build link copied');
    });
    expect(cards()).toHaveLength(1);
    expect(cards()[0].querySelector('.toast__count')?.textContent).toContain('×2');
    expect(cards()[0].querySelector('.toast__count .sr-only')?.textContent).toBe(' (2 times)');
    act(() => {
      vi.advanceTimersByTime(SUCCESS_TOAST_MS - 100);
    });
    expect(cards()).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(cards()).toHaveLength(0);
  });
});

/**
 * Toasts over a modal (audit a11y-07, correction 3): showModal() makes the
 * shell inert and paints the dialog above a popover shown earlier, so the
 * region re-hosts inside the topmost modal layer (lib/modalLayers) and back.
 * A toast already on screen is carried over without a second announcement;
 * one raised inside the modal lands in the live list and is announced once.
 */
describe('Toaster over a modal dialog', () => {
  let root: Root;
  let container: HTMLElement;
  let modal: HTMLDialogElement;
  let popModal: (() => void) | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    session.canAccessAdmin = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    modal = document.createElement('dialog');
    modal.setAttribute('open', '');
    modal.innerHTML = '<button type="button" id="modal-close">Close drawer</button>';
    document.body.appendChild(modal);
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
    act(() => popModal?.());
    popModal = null;
    act(() => root.unmount());
    container.remove();
    modal.remove();
    act(() => clearToasts());
    vi.useRealTimers();
  });

  const region = () => document.querySelector<HTMLElement>('section.toast-region');
  const liveList = () => region()?.querySelector<HTMLElement>('[role="status"][aria-live="polite"]') ?? null;
  const openModal = () => act(() => {
    popModal = pushModalLayer(modal);
  });
  const closeModal = () => act(() => {
    popModal?.();
    popModal = null;
  });

  it('moves the same region element into the topmost modal layer, and back to <body> once it closes', () => {
    const shellRegion = region();
    expect(shellRegion?.parentElement?.parentElement).toBe(document.body);
    openModal();
    // Moved, not re-created: the element (and whatever a dialog recorded in
    // it as its opener) survives the round trip.
    expect(region()).toBe(shellRegion);
    expect(modal.contains(region())).toBe(true);
    expect(document.querySelectorAll('section.toast-region')).toHaveLength(1);
    expect(region()?.getAttribute('aria-label')).toBe('Notifications');
    expect(liveList()).not.toBeNull();
    closeModal();
    expect(region()).toBe(shellRegion);
    expect(region()?.parentElement?.parentElement).toBe(document.body);
    expect(modal.querySelector('section.toast-region')).toBeNull();
  });

  it('is back in <body> before a closing dialog hands focus back to a control inside it', () => {
    act(() => {
      toast.success('Build saved', { auditEventId: 'evt-0001' });
    });
    openModal();
    const link = document.querySelector<HTMLAnchorElement>('a.toast__link')!;
    // What the modal-layer pop does first; the dialog's focus return follows
    // in the same task, before React re-renders anything.
    popModal?.();
    popModal = null;
    expect(modal.contains(link)).toBe(false);
    expect(link.isConnected).toBe(true);
    act(() => link.focus());
    expect(document.activeElement).toBe(link);
  });

  it('keeps a toast already on screen visible when a modal opens, without announcing it again', () => {
    act(() => {
      toast.success('Build saved');
      toast.error('Copy failed');
    });
    expect(liveList()?.textContent).toContain('Build saved');
    const failure = document.querySelector<HTMLElement>('.toast--error')!;
    const success = liveList()!.querySelector<HTMLElement>('.toast')!;
    expect(failure.getAttribute('role')).toBe('alert');

    openModal();
    // The same card elements, moved with the region: still shown, never
    // re-created (a re-created card would be new, announced content).
    const carried = [...modal.querySelectorAll<HTMLElement>('.toast')];
    expect(carried).toEqual([failure, success]);
    // A moved role=alert is an inserted alert, which is announced: the
    // failure already on screen drops the role. The success stays in the
    // polite list, whose existing content is not a change.
    expect(failure.hasAttribute('role')).toBe(false);
    expect(success.closest('[role="status"][aria-live="polite"]')).toBe(liveList());
    expect(modal.querySelectorAll('[role="alert"]')).toHaveLength(0);
  });

  it('announces a toast raised while the modal is open once, in the live list that existed before it', () => {
    openModal();
    const list = liveList();
    expect(list?.childElementCount).toBe(0);
    act(() => {
      toast.success('Assigned 12 borrowers');
    });
    expect(liveList()).toBe(list);
    expect(list?.querySelectorAll('.toast')).toHaveLength(1);
    expect(list?.textContent).toContain('Assigned 12 borrowers');
    expect(document.querySelectorAll('.toast')).toHaveLength(1);
  });

  it('keeps the focus hand-off inside the dialog after a dismiss, never on the inert page', () => {
    const main = document.createElement('main');
    main.id = 'main-content';
    main.tabIndex = -1;
    main.innerHTML = '<h1 tabindex="-1">Lead Queue</h1><button type="button" id="page-origin">Assign</button>';
    document.body.appendChild(main);
    try {
      openModal();
      act(() => {
        toast.error('Assignment failed');
      });
      // Focus reached the toast from the page (inert behind a real modal).
      act(() => document.getElementById('page-origin')?.focus());
      const close = modal.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]')!;
      act(() => close.focus());
      act(() => close.click());
      expect(document.activeElement?.id).toBe('modal-close');
    } finally {
      main.remove();
    }
  });
});
