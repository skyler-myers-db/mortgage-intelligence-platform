/**
 * Topmost-layer Escape stack (audit 2026-09-21 `runtime-v2`).
 *
 * Before this module every overlay bound its own `window` keydown listener:
 * the evidence drawer, the command palette, the Genie proof drawer, filter
 * menus and the floating Genie panel all reacted to the SAME Escape keypress,
 * so one key closed two layers — and, because closing Genie used to abort its
 * in-flight turn, dismissing a drawer silently destroyed a running answer.
 *
 * Contract:
 *   - A layer pushes a handler when it opens and pops it when it closes.
 *   - One capture-phase `window` listener owns Escape. It walks the stack from
 *     the top; the first layer that accepts the key consumes it
 *     (`preventDefault` + `stopImmediatePropagation`), so no lower layer and
 *     no other Escape listener sees that keypress.
 *   - A handler returns `false` to DECLINE. Modal layers never decline. The
 *     non-modal Genie panel declines when focus is outside it, which lets the
 *     key fall through to the layer below (or to the page) instead of being
 *     swallowed by a panel the user is not working in.
 *   - A keypress some inner widget already handled (`defaultPrevented`) is
 *     left alone.
 *
 * Deliberately not a React context: layers live in different subtrees and
 * portals, and the ordering that matters is open order, not tree order.
 */

export type EscapeLayerHandler = (event: KeyboardEvent) => boolean | void;

interface EscapeLayer {
  id: number;
  handler: EscapeLayerHandler;
}

const layers: EscapeLayer[] = [];
let nextLayerId = 1;
let listening = false;

function onWindowKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  // Snapshot: a handler that closes its layer pops the stack while we walk it.
  const ordered = [...layers].reverse();
  for (const layer of ordered) {
    if (layer.handler(event) === false) continue;
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }
}

function syncListener(): void {
  if (typeof window === 'undefined') return;
  if (layers.length > 0 && !listening) {
    window.addEventListener('keydown', onWindowKeyDown, true);
    listening = true;
  } else if (layers.length === 0 && listening) {
    window.removeEventListener('keydown', onWindowKeyDown, true);
    listening = false;
  }
}

/**
 * Register `handler` as the new topmost Escape layer. Returns the pop
 * function; calling it more than once is a no-op, so it is safe as a React
 * effect cleanup.
 */
export function pushEscapeLayer(handler: EscapeLayerHandler): () => void {
  const id = nextLayerId;
  nextLayerId += 1;
  layers.push({ id, handler });
  syncListener();
  return () => {
    const index = layers.findIndex((layer) => layer.id === id);
    if (index === -1) return;
    layers.splice(index, 1);
    syncListener();
  };
}

/** Number of open layers. Diagnostics and tests only. */
export function escapeLayerCount(): number {
  return layers.length;
}
