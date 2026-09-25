/**
 * The open modal `<dialog>` layers, in open order (audit 2026-09-21
 * `stack-05` / `a11y-07` step 2).
 *
 * `useModalDialog` pushes a dialog right after `showModal()` and pops it when
 * the dialog closes. Anything that must stay usable while a modal is open
 * reads the TOPMOST layer: `showModal()` makes every node outside that dialog
 * inert, so a region rendered in the shell (the toast region) would be
 * unreachable, unannounced and painted beneath the dialog. The Toaster
 * re-hosts itself inside `topModalLayer()` instead.
 *
 * Deliberately not a React context, like lib/escapeStack: the dialogs live
 * in different subtrees and portals, and the order that matters is open
 * order, not tree order.
 */

const layers: HTMLElement[] = [];
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/**
 * Register `element` as the new topmost modal layer. Returns the pop
 * function; calling it more than once is a no-op, so it is safe as a React
 * effect cleanup.
 */
export function pushModalLayer(element: HTMLElement): () => void {
  layers.push(element);
  notify();
  let popped = false;
  return () => {
    if (popped) return;
    popped = true;
    const index = layers.lastIndexOf(element);
    if (index === -1) return;
    layers.splice(index, 1);
    notify();
  };
}

/** The dialog opened last and still open, or null when no modal is open. */
export function topModalLayer(): HTMLElement | null {
  return layers.length > 0 ? layers[layers.length - 1] : null;
}

/** `useSyncExternalStore` subscription: called after every push and pop. */
export function subscribeModalLayers(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Number of open modal layers. Diagnostics and tests only. */
export function modalLayerCount(): number {
  return layers.length;
}
