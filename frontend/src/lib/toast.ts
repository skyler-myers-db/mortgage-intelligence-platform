/**
 * The shell's transient feedback store (2026-09-21 audit states-07, slice 1).
 *
 * Success and failure feedback used to live in five ad hoc places (button
 * labels that flipped for two seconds, a routing chip, table-foot strings):
 * it sat below the fold, overwrote itself, and never linked to the audit row
 * a write had just produced. Writes now raise a toast here and the one
 * `<Toaster/>` in the app shell renders them.
 *
 * Rules the store enforces, so no caller can produce a stacking storm:
 *
 *   - Coalesce: a toast identical to one already visible (same tone, title,
 *     detail and audit event) bumps that toast's `count` and `revision`
 *     instead of stacking a copy; the Toaster restarts its timer. A toast
 *     with an ACTION never coalesces, and nothing coalesces into one: each
 *     action is its own closure (Retry this, Undo that).
 *   - Cap: at most `TOAST_LIMIT` toasts are visible. A new one evicts the
 *     oldest success first, then the oldest info, then the oldest failure, so
 *     a pending failure is never pushed out by a burst of confirmations.
 *
 * The platform (W5b, the states-07 item 1 and states-08 item 3
 * prerequisites; deviation:toast-actions-and-path): an `info` tone (neutral,
 * never auto-dismissed), one optional action per toast that runs once and
 * dismisses it, and `retryAfterMs`, which holds that action disabled behind a
 * countdown while a 429's Retry-After runs (`actionReadyAt`). The consumers
 * (the decision receipt, the bulk result, the 429 Retry, assignment Undo)
 * arrive with their own lanes.
 *
 * Deliberately a module store, not a React context: the writes that raise a
 * toast live in lazy routes, and the region lives in the shell.
 *
 * Toast text is shown to the signed-in person only; it is never telemetry.
 * It is actor-scoped: a build name, a loan officer's address or an audit
 * event id raised for one operator must not survive into the next one's
 * session on a shared machine (a failure toast stays until dismissed), so
 * the store registers with the shell's actor-change reset below.
 */
import { registerActorScopedMemoryCache } from './actorScopedMemoryCaches';

export type ToastTone = 'success' | 'error' | 'info';

export interface ToastAction {
  /** The button's visible label (Retry, Undo, View receipt). */
  label: string;
  /** Runs once, on the first activation; the toast is then dismissed. */
  onAction: () => void;
}

export interface ToastOptions {
  /** A second line under the title. */
  detail?: string | null;
  /** The ledger row this write produced; the Toaster links to it. */
  auditEventId?: string | null;
  /** One action button. A toast with an action never coalesces. */
  action?: ToastAction | null;
  /** A Retry-After: the action waits this long (with a countdown) before it
   *  is live. Ignored without an action, and when not positive. */
  retryAfterMs?: number | null;
}

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  detail: string | null;
  auditEventId: string | null;
  /** How many identical toasts this one stands for (coalesced). */
  count: number;
  /** Bumped on every coalesce, so the Toaster restarts the dismiss timer. */
  revision: number;
  action: ToastAction | null;
  /** Epoch ms from which the action is live; null when it is live at once. */
  actionReadyAt: number | null;
}

export const TOAST_LIMIT = 3;

/** Which tone the cap drops first. */
const EVICTION_ORDER: readonly ToastTone[] = ['success', 'info', 'error'];

type Listener = () => void;

let toasts: readonly Toast[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function publish(next: readonly Toast[]): void {
  toasts = next;
  for (const listener of [...listeners]) listener();
}

function sameToast(toast: Toast, tone: ToastTone, title: string, detail: string | null, auditEventId: string | null) {
  return toast.action === null
    && toast.tone === tone
    && toast.title === title
    && toast.detail === detail
    && toast.auditEventId === auditEventId;
}

function evictOne(current: readonly Toast[]): readonly Toast[] {
  for (const tone of EVICTION_ORDER) {
    const index = current.findIndex((toast) => toast.tone === tone);
    if (index !== -1) return current.filter((_, position) => position !== index);
  }
  return current.slice(1);
}

/** Raise a toast; returns its id (the id of the toast it coalesced into, if any). */
export function showToast(tone: ToastTone, title: string, options: ToastOptions = {}): number {
  const detail = options.detail ?? null;
  const auditEventId = options.auditEventId ?? null;
  const action = options.action ?? null;
  const retryAfterMs = options.retryAfterMs ?? null;
  const existing = action === null
    ? toasts.find((toast) => sameToast(toast, tone, title, detail, auditEventId))
    : undefined;
  if (existing) {
    publish(toasts.map((toast) => (
      toast === existing ? { ...toast, count: toast.count + 1, revision: toast.revision + 1 } : toast
    )));
    return existing.id;
  }
  const actionReadyAt = action !== null && retryAfterMs !== null && retryAfterMs > 0 ? Date.now() + retryAfterMs : null;
  let current = toasts;
  while (current.length >= TOAST_LIMIT) current = evictOne(current);
  const id = nextId;
  nextId += 1;
  publish([...current, { id, tone, title, detail, auditEventId, count: 1, revision: 0, action, actionReadyAt }]);
  return id;
}

export function dismissToast(id: number): void {
  if (!toasts.some((toast) => toast.id === id)) return;
  publish(toasts.filter((toast) => toast.id !== id));
}

/** Drop every toast (an actor change; tests). */
export function clearToasts(): void {
  if (toasts.length > 0) publish([]);
}

// AppShell calls clearActorScopedMemoryCaches when the signed-in actor changes.
registerActorScopedMemoryCache(clearToasts);

export function getToasts(): readonly Toast[] {
  return toasts;
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export const toast = {
  success: (title: string, options?: ToastOptions) => showToast('success', title, options),
  error: (title: string, options?: ToastOptions) => showToast('error', title, options),
  info: (title: string, options?: ToastOptions) => showToast('info', title, options),
};
