/**
 * The Lead Queue's single-row write failures on the shell toast region
 * (audit states-07 item 2; the wave-3 queue-place #0 remainder). An approve,
 * reject or assign failure, and a gate refusal, used to write one string
 * into a `.table-error` alert under the table: below the fold on a long
 * queue, overwritten by the next write, and invisible while the approve
 * review's modal dialog made the rest of the page inert. The shell Toaster
 * keeps a failure until it is dismissed and moves into the topmost dialog.
 *
 * The detail of a failed request is describeApiError's buyer-safe body
 * (never the transport message). The vocabulary is loaded on the failure
 * only, through AsyncFailure's existing lazy chunk (failureSentence): a
 * static import would add it to both routes that render the table
 * (lead-queue and segment-intelligence), and a new dynamic import of
 * lib/describeApiError itself split the transport out of the entry chunk
 * (+0.36 KiB br of initial JS, measured; DescribedError.tsx records the same
 * trap). If that chunk cannot load (offline, a stale deploy), the toast keeps
 * the message the table showed before, which for an offline write is the
 * same 'You are offline. Reconnect, then try again.'
 *
 * Toast text is actor-scoped and shown only to the signed-in person; it is
 * never telemetry (lib/toast).
 */
import { ApiError } from '../../lib/apiTransport';
import { toast } from '../../lib/toast';

/** A write that failed on the wire: describeApiError's body for an ApiError. */
export function toastWriteFailure(title: string, error: unknown, subject: string): void {
  const fallback = error instanceof Error && error.message ? error.message : null;
  if (!(error instanceof ApiError)) {
    toast.error(title, { detail: fallback });
    return;
  }
  // A vite:preloadError handler may resolve a failed chunk to undefined.
  void (import('../ui/AsyncFailure') as Promise<typeof import('../ui/AsyncFailure') | undefined>).then(
    (module) => {
      toast.error(title, { detail: (module && module.failureSentence(error, subject)) || fallback });
    },
    () => {
      toast.error(title, { detail: fallback });
    },
  );
}

/** A refusal before any request (the approver or campaign-binding gate, a decision on the wire). */
export function toastWriteRefusal(title: string, detail: string): void {
  toast.error(title, { detail });
}
