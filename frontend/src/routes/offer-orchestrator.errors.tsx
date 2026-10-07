import { Button } from '../components/Primitives';
import { DescribedErrorBody } from '../components/ui/DescribedError';
import { ApiError } from '../lib/api';
import { FixedCopyError } from './offer-orchestrator.queries';

/**
 * The Offer Orchestrator's failures in buyer-safe copy (2026-09-21 audit
 * states-04). The route used to print `err.message` after each lead phrase
 * ("Couldn't write approval: <server detail>"), which put transport jargon
 * and server detail strings in front of a reviewer. Now a failure keeps its
 * lead phrase and its cause is described by the shared vocabulary
 * (DescribedErrorBody, lib/describeApiError through its lazy chunk), never
 * the raw message.
 *
 * Two of the route's own errors carry a fixed sentence the reviewer acts on
 * (a refresh landed between the reads; the campaign proof went stale): that
 * sentence is shown as written, being app copy, not server detail
 * (FixedCopyError).
 *
 * A 409 on approve or reject means the decision cites a draft that is no
 * longer current (its proof or its campaign variant changed). The alert
 * then offers an explicit Regenerate draft: one user-initiated, audited
 * draft read, never run automatically and never advice to reload the page
 * (a reload re-runs every audited read of this surface).
 */

/** A failed governed write: the lead phrase and what caused it. */
export interface OfferWriteFailure {
  lead: string;
  cause: unknown;
}

export const OFFER_CONFLICT_BODY = 'The draft this decision cites is no longer current.';

export function isConflictError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/** The cause sentence: fixed app copy as written, otherwise the shared vocabulary. */
export function OfferFailureBody({ cause, subject }: { cause: unknown; subject: string }) {
  if (cause instanceof FixedCopyError) return cause.message;
  return <DescribedErrorBody error={cause} subject={subject} />;
}

/**
 * A write failure inside the decision bar's alert: the lead phrase, then the
 * cause; a 409 adds an explicit Regenerate draft (when the route offers it).
 */
export function OfferWriteFailureCopy({
  failure,
  onRegenerate,
  regenerateDisabled = false,
}: {
  failure: OfferWriteFailure;
  onRegenerate?: () => void;
  regenerateDisabled?: boolean;
}) {
  const conflict = isConflictError(failure.cause);
  return (
    <>
      {failure.lead}
      {' '}
      {conflict ? OFFER_CONFLICT_BODY : <OfferFailureBody cause={failure.cause} subject="the decision" />}
      {conflict && onRegenerate && (
        <div className="chip-row mt-2">
          <Button variant="default" size="sm" icon="play" onClick={onRegenerate} disabled={regenerateDisabled}>
            Regenerate draft
          </Button>
        </div>
      )}
    </>
  );
}
