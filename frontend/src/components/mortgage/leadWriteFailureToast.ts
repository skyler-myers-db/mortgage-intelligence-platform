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
 * trap). If that chunk cannot load (a stale deploy), the toast keeps the
 * message the table showed before.
 *
 * A client-side failure (offline, unreachable, an ended session, an
 * unreadable response) never fetches the chunk: offline the fetch would
 * fail too, and for these the ApiError's message already IS describeApiError's
 * body (both are lib/apiFailure's CLIENT_FAILURE_MESSAGES, e.g. 'You are
 * offline. Reconnect, then try again.').
 *
 * Toast text is actor-scoped and shown only to the signed-in person; it is
 * never telemetry (lib/toast).
 */
import { ApiError, clientFailureReason } from '../../lib/apiTransport';
import { toast } from '../../lib/toast';

/**
 * describeApiError's body for `error`, from AsyncFailure's lazy chunk; null
 * when the chunk resolved to nothing (a vite:preloadError handler). Awaited
 * inside a function on purpose: a `.then` chained straight onto `import()` is
 * moved INSIDE Vite's preload wrapper by the bundler, so a stylesheet
 * dependency that fails to load rejected the outer promise unhandled and
 * the toast never showed (measured on the build, offline).
 */
async function failureSentence(error: ApiError, subject: string): Promise<string | null> {
  const module: typeof import('../ui/AsyncFailure') | undefined = await import('../ui/AsyncFailure');
  return module ? module.failureSentence(error, subject) : null;
}

/** A write that failed on the wire: describeApiError's body for an ApiError. */
export function toastWriteFailure(title: string, error: unknown, subject: string): void {
  const fallback = error instanceof Error && error.message ? error.message : null;
  if (!(error instanceof ApiError) || clientFailureReason(error) !== null) {
    toast.error(title, { detail: fallback });
    return;
  }
  failureSentence(error, subject).then(
    (sentence) => {
      toast.error(title, { detail: sentence || fallback });
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
