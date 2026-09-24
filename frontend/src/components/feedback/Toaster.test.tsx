/**
 * @vitest-environment happy-dom
 *
 * The shell toast region (audit states-07 slice 1) at the component grain:
 * live-region roles, the audit-event link, dismiss, auto-dismiss with
 * pause-on-hover / pause-on-focus, sticky failures and the coalesced count.
 * happy-dom has no Popover API, so this also covers the fixed-position
 * fallback path; feedback-guard.fixture.spec.ts proves the top-layer path.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

  const region = () => container.querySelector('section.toast-region');
  const cards = () => [...container.querySelectorAll<HTMLElement>('.toast')];

  it('is one labelled region with a persistent polite status list, empty at rest', () => {
    expect(region()?.getAttribute('aria-label')).toBe('Notifications');
    expect(region()?.getAttribute('popover')).toBe('manual');
    const list = region()?.querySelector('.toast-region__list');
    expect(list?.getAttribute('role')).toBe('status');
    expect(list?.getAttribute('aria-live')).toBe('polite');
    expect(list?.childElementCount).toBe(0);
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('puts a confirmation in the status list and a failure in its own alert', () => {
    act(() => {
      toast.success('Build saved', { detail: 'IL refi cohort' });
      toast.error('Copy failed');
    });
    const list = region()?.querySelector('[role="status"]');
    expect(list?.textContent).toContain('Build saved');
    expect(list?.textContent).toContain('IL refi cohort');
    const alert = container.querySelector('.toast--error');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Copy failed');
    expect(list?.contains(alert as Node)).toBe(false);
  });

  it('links the audit event for an actor who can open the explorer, and shows the id otherwise', () => {
    act(() => {
      toast.success('Build saved', { auditEventId: 'evt-0001' });
    });
    const link = container.querySelector<HTMLAnchorElement>('a.toast__link');
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
    expect(container.querySelector('a.toast__link')).toBeNull();
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
    const dismiss = container.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
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
    const close = container.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
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
      const closes = () => [...container.querySelectorAll<HTMLButtonElement>('button[aria-label="Dismiss notification"]')];
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
    const close = () => container.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]');
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
