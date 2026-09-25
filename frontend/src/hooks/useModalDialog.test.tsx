/**
 * @vitest-environment happy-dom
 *
 * useModalDialog (audit 2026-09-21 stack-05 / a11y-07 step 2): the one hook
 * every modal surface opens its native <dialog> through. happy-dom has no top
 * layer and no inert, so modality itself is proven in
 * tests/e2e/fixture/overlays.fixture.spec.ts; this file pins the wiring:
 * showModal after the opener is recorded, the modal-layer stack, native
 * cancel / close handling, the backdrop press rules and the focus-return
 * fallback chain.
 */
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { escapeLayerCount } from '../lib/escapeStack';
import { modalLayerCount, topModalLayer } from '../lib/modalLayers';
import { useModalDialog, type ModalBackdrop } from './useModalDialog';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface HarnessProps {
  open: boolean;
  onDismiss: () => void;
  dismissible?: boolean;
  backdrop?: ModalBackdrop;
}

function Harness({ open, onDismiss, dismissible, backdrop = 'outside' }: HarnessProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useModalDialog({ open, dialogRef, initialFocusRef: closeRef, onDismiss, dismissible, backdrop });
  return (
    <dialog ref={dialogRef} className="drawer" aria-label="Harness" data-testid="harness">
      <button ref={closeRef} type="button">Close</button>
      <div className="panel-body">Body</div>
    </dialog>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('useModalDialog', () => {
  let root: Root;
  const onDismiss = vi.fn();

  beforeEach(() => {
    document.body.innerHTML = [
      '<main id="main-content" tabindex="-1"><h1 tabindex="-1">Lead Queue</h1>',
      '<section id="region"><h2 tabindex="-1">Ranked borrowers</h2><button id="opener">Open</button></section>',
      '</main><div id="root"></div>',
    ].join('');
    root = createRoot(document.getElementById('root') as HTMLElement);
    onDismiss.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  const dialog = () => document.querySelector<HTMLDialogElement>('dialog[data-testid="harness"]')!;
  const opener = () => document.getElementById('opener') as HTMLButtonElement;

  async function render(props: Partial<HarnessProps> & { open: boolean }): Promise<void> {
    await act(async () => {
      root.render(<Harness onDismiss={onDismiss} {...props} />);
    });
    await settle();
  }

  it('records the opener before showModal moves focus, then pushes one modal layer', async () => {
    let focusedAtShowModal: Element | null = null;
    const showModal = vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function (this: HTMLDialogElement) {
      focusedAtShowModal = document.activeElement;
      this.setAttribute('open', '');
      // The platform moves focus into the dialog as part of showModal().
      this.querySelector('button')?.focus();
    });
    opener().focus();
    await render({ open: true });

    expect(showModal).toHaveBeenCalledOnce();
    expect(focusedAtShowModal).toBe(opener());
    expect(dialog().open).toBe(true);
    expect(modalLayerCount()).toBe(1);
    expect(topModalLayer()).toBe(dialog());
    // No role and no aria-modal: both are implicit on a modal <dialog>.
    expect(dialog().hasAttribute('role')).toBe(false);
    expect(dialog().hasAttribute('aria-modal')).toBe(false);

    await render({ open: false });
    expect(dialog().open).toBe(false);
    expect(modalLayerCount()).toBe(0);
    // Focus goes back to the opener even though showModal had moved it.
    expect(document.activeElement).toBe(opener());
  });

  it('closes FIRST, then pops the layer, then returns focus', async () => {
    const order: string[] = [];
    opener().focus();
    await render({ open: true });
    const close = vi.spyOn(dialog(), 'close').mockImplementation(function (this: HTMLDialogElement) {
      order.push(`close (layers ${modalLayerCount()})`);
      this.removeAttribute('open');
    });
    opener().addEventListener('focus', () => order.push(`focus (layers ${modalLayerCount()}, open ${dialog().open})`));

    await render({ open: false });
    expect(close).toHaveBeenCalledOnce();
    expect(order).toEqual(['close (layers 1)', 'focus (layers 0, open false)']);
  });

  it('prevents the native cancel and routes one Escape to exactly one onDismiss', async () => {
    await render({ open: true });
    expect(escapeLayerCount()).toBe(1);

    // A cancel with no keypress behind it (Android back, a CloseWatcher).
    const cancel = new Event('cancel', { cancelable: true });
    dialog().dispatchEvent(cancel);
    expect(cancel.defaultPrevented).toBe(true);
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(dialog().open).toBe(true);

    // A keyboard Escape: the shared stack consumes it; a cancel the browser
    // still fires for the same keypress in the same task is not a second one.
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    const echoed = new Event('cancel', { cancelable: true });
    dialog().dispatchEvent(echoed);
    expect(echoed.defaultPrevented).toBe(true);
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });

  it('reports a browser-forced close of a dismissible dialog, and re-opens a blocking one', async () => {
    await render({ open: true });
    dialog().close();
    expect(onDismiss).toHaveBeenCalledOnce();

    await render({ open: false });
    onDismiss.mockClear();
    await render({ open: true, dismissible: false });
    dialog().close();
    expect(onDismiss).not.toHaveBeenCalled();
    expect(dialog().open).toBe(true);
    expect(modalLayerCount()).toBe(1);
  });

  it('never reports its own close, including an unmount while open', async () => {
    await render({ open: true });
    await render({ open: false });
    await render({ open: true });
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    expect(onDismiss).not.toHaveBeenCalled();
    expect(modalLayerCount()).toBe(0);
    expect(escapeLayerCount()).toBe(0);
  });

  function press(target: Element, point: { x: number; y: number }): void {
    target.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: point.x, clientY: point.y }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: point.x, clientY: point.y }));
  }

  function panelBox(el: HTMLElement): void {
    vi.spyOn(el, 'getBoundingClientRect').mockReturnValue(DOMRect.fromRect({ x: 980, y: 0, width: 460, height: 900 }));
  }

  it("'outside': a press on the backdrop dismisses, a press on the panel's own padding does not", async () => {
    await render({ open: true, backdrop: 'outside' });
    panelBox(dialog());

    press(dialog(), { x: 1200, y: 400 });
    expect(onDismiss).not.toHaveBeenCalled();

    press(dialog().querySelector('.panel-body')!, { x: 8, y: 8 });
    expect(onDismiss).not.toHaveBeenCalled();

    press(dialog(), { x: 8, y: 8 });
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it('a press must both start and end on the backdrop', async () => {
    await render({ open: true, backdrop: 'outside' });
    panelBox(dialog());
    // Down inside the panel (a text selection), up on the backdrop.
    dialog().querySelector('.panel-body')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 1000, clientY: 10 }));
    dialog().dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 8, clientY: 8 }));
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("'self': the dialog's own area is the backdrop; false never dismisses on a press", async () => {
    await render({ open: true, backdrop: 'self' });
    panelBox(dialog());
    press(dialog(), { x: 1200, y: 400 });
    expect(onDismiss).toHaveBeenCalledOnce();

    await render({ open: false });
    onDismiss.mockClear();
    await render({ open: true, backdrop: false });
    press(dialog(), { x: 8, y: 8 });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  describe('focus return when the opener is gone', () => {
    it('falls back to the recorded region heading', async () => {
      opener().focus();
      await render({ open: true });
      opener().remove();
      await render({ open: false });
      expect(document.activeElement?.textContent).toBe('Ranked borrowers');
    });

    it('then to the page h1, then to #main-content', async () => {
      opener().focus();
      await render({ open: true });
      document.getElementById('region')!.remove();
      await render({ open: false });
      expect(document.activeElement?.tagName).toBe('H1');

      // Open again from the h1 itself (the region is gone), then lose the h1.
      await render({ open: true });
      document.querySelector('h1')!.remove();
      await render({ open: false });
      expect(document.activeElement?.id).toBe('main-content');
    });
  });
});
