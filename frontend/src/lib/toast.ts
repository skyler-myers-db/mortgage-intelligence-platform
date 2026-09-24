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
 *     instead of stacking a copy; the Toaster restarts its timer.
 *   - Cap: at most `TOAST_LIMIT` toasts are visible. A new one evicts the
 *     oldest success first, so a pending failure is never pushed out by a
 *     burst of confirmations.
 *
 * Deliberately a module store, not a React context: the writes that raise a
 * toast live in lazy routes, and the region lives in the shell. Undo (a
 * compensating audited write for assignment only) and the useMutation
 * migration are slice 2.
 *
 * Toast text is shown to the signed-in person only; it is never telemetry.
 * It is actor-scoped: a build name, a loan officer's address or an audit
 * event id raised for one operator must not survive into the next one's
 * session on a shared machine (a failure toast stays until dismissed), so
 * the store registers with the shell's actor-change reset below.
 */
import { registerActorScopedMemoryCache } from './actorScopedMemoryCaches';

export type ToastTone = 'success' | 'error';

export interface ToastOptions {
  /** A second line under the title. */
  detail?: string | null;
  /** The ledger row this write produced; the Toaster links to it. */
  auditEventId?: string | null;
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
}

export const TOAST_LIMIT = 3;

type Listener = () => void;

let toasts: readonly Toast[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function publish(next: readonly Toast[]): void {
  toasts = next;
  for (const listener of [...listeners]) listener();
}

function sameToast(toast: Toast, tone: ToastTone, title: string, detail: string | null, auditEventId: string | null) {
  return toast.tone === tone
    && toast.title === title
    && toast.detail === detail
    && toast.auditEventId === auditEventId;
}

function evictOne(current: readonly Toast[]): readonly Toast[] {
  const oldestSuccess = current.findIndex((toast) => toast.tone === 'success');
  const index = oldestSuccess === -1 ? 0 : oldestSuccess;
  return current.filter((_, position) => position !== index);
}

/** Raise a toast; returns its id (the id of the toast it coalesced into, if any). */
export function showToast(tone: ToastTone, title: string, options: ToastOptions = {}): number {
  const detail = options.detail ?? null;
  const auditEventId = options.auditEventId ?? null;
  const existing = toasts.find((toast) => sameToast(toast, tone, title, detail, auditEventId));
  if (existing) {
    publish(toasts.map((toast) => (
      toast === existing ? { ...toast, count: toast.count + 1, revision: toast.revision + 1 } : toast
    )));
    return existing.id;
  }
  let current = toasts;
  while (current.length >= TOAST_LIMIT) current = evictOne(current);
  const id = nextId;
  nextId += 1;
  publish([...current, { id, tone, title, detail, auditEventId, count: 1, revision: 0 }]);
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
};
