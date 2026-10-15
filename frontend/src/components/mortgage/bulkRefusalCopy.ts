import { ApiError } from '../../lib/apiTransport';

/**
 * Fixed copy for one bulk row the server refused (audit states-04 residual).
 *
 * A bulk run's report and its canary line used to print the raw server
 * message ("Approval requires the audited generated draft proof"), which is
 * transport text, not product copy. The writers store this instead: a sync
 * classifier from the HTTP status (and the client's session_expired reason)
 * to one short sentence, so the lazy bulk chunk never needs describeApiError.
 * It reads after "refused: ". A governed-text 422 carries no reason, so the
 * 422 sentence names both things the screen reads: the row's draft and the
 * reason typed for the run. The sentences are short on purpose: they ride
 * the leadsQuery chunk (lane cap +0.10 KiB br).
 */
const BY_STATUS: Readonly<Record<number, string>> = {
  403: 'your role cannot decide it',
  409: 'it changed; review it alone',
  422: 'draft or reason failed the checks',
  429: 'too many at once; retry soon',
};

/** Also the copy for a 2xx that did not record the decision (`rejected: false`). */
export const BULK_UNRECORDED = 'not recorded; retry it';

export function bulkRefusalCopy(err: unknown): string {
  if (!(err instanceof ApiError)) return BULK_UNRECORDED;
  return err.reason === 'session_expired' ? 'session ended; sign in again' : BY_STATUS[err.status ?? 0] || BULK_UNRECORDED;
}
