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
 *   - The listener runs at the window CAPTURE phase, so it sees the keypress
 *     before any element-level or bubble-phase handler. An accepting layer
 *     therefore pre-empts those handlers. That is the point for overlays,
 *     and it is also why every Escape-closable overlay must be ON the stack:
 *     an Escape handler bound to an element or to `window` at bubble phase
 *     never runs while a layer accepts. (Known off-stack handler:
 *     `analytics.equity-scatter` cluster `onKeyDown` — a follow-up. The
 *     MultiFilterSelect window listener moved onto the stack with the
 *     shared listbox hook, audit a11y-02.)
 *   - The `defaultPrevented` check only defers to another capture-phase
 *     `window` listener registered before this one; nothing else runs
 *     earlier.
 *   - A dismissal that is NOT a keypress (a native `<dialog>` `cancel` from
 *     the Android back gesture or a CloseWatcher) goes through
 *     `dismissTopLayer()`: the same walk, run once. A `cancel` the browser
 *     fires for an Escape this stack already consumed in the same task is
 *     ignored, so one Escape never closes two layers (audit a11y-07 step 2).
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
/** True from an Escape this stack consumed until the end of that task. */
let escapeConsumedThisTask = false;

/** Run the topmost accepting layer. Returns true when one accepted. */
function runTopLayer(event: KeyboardEvent): boolean {
  // Snapshot: a handler that closes its layer pops the stack while we walk it.
  const ordered = [...layers].reverse();
  for (const layer of ordered) {
    if (layer.handler(event) === false) continue;
    return true;
  }
  return false;
}

function onWindowKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (!runTopLayer(event)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (escapeConsumedThisTask) return;
  escapeConsumedThisTask = true;
  // A task, not a microtask: a browser that still fires the dialog's
  // `cancel` for this keypress does so later in the same task.
  window.setTimeout(() => {
    escapeConsumedThisTask = false;
  }, 0);
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

/**
 * Close the topmost layer once, for a dismissal that is not an Escape
 * keypress: the native `cancel` of a modal `<dialog>` (Android back, a
 * CloseWatcher). Layers see the same Escape they would on a keypress. A
 * no-op when this task already consumed an Escape keypress. Returns true
 * when a layer accepted.
 */
export function dismissTopLayer(): boolean {
  if (escapeConsumedThisTask || typeof window === 'undefined') return false;
  return runTopLayer(new KeyboardEvent('keydown', { key: 'Escape' }));
}

/** Number of open layers. Diagnostics and tests only. */
export function escapeLayerCount(): number {
  return layers.length;
}
