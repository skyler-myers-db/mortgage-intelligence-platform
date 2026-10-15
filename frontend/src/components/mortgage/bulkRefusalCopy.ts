import { ApiError } from '../../lib/apiTransport';

/**
 * Fixed copy for one bulk row the server refused (audit states-04 residual).
 *
 * A bulk run's report and its canary line used to print the raw server
 * message ("Approval requires the audited generated draft proof"), which is
 * transport text, not product copy. The writers store this instead: a sync
 * classifier from the HTTP status to one sentence, so the lazy bulk chunk
 * never needs describeApiError. It reads after "refused: ".
 */
const BY_STATUS: Readonly<Record<number, string>> = {
  403: 'your role cannot make this decision',
  409: 'it changed after you selected it; review it on its own',
  422: 'it did not pass the review checks; open it to review the draft and reason',
  429: 'too many decisions at once; wait a minute, then retry it',
};

const UNRECORDED = 'the server did not record it; retry it';

export function bulkRefusalCopy(err: unknown): string {
  const status = err instanceof ApiError ? err.status : null;
  return (status !== null && BY_STATUS[status]) || UNRECORDED;
}
