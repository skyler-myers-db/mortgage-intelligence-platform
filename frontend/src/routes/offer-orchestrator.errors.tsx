import { Button } from '../components/Primitives';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';
import { ApiError } from '../lib/api';
import type { OfferRefusalScope } from '../lib/offerRefusals';
import { FixedCopyError } from './offer-orchestrator.queries';

/**
 * The Offer Orchestrator's failures in buyer-safe copy (2026-09-21 audit
 * states-04). The route used to print `err.message` after each lead phrase
 * ("Couldn't write approval: <server detail>"), which put transport jargon
 * and server detail strings in front of a reviewer. Now a failure keeps its
 * lead phrase and its cause is described, never as the raw message:
 *
 * - The route's own fixed sentences (a refresh landed between the reads; the
 *   campaign proof went stale) are shown as written, being app copy, not
 *   server detail (FixedCopyError).
 * - A 409 or 422 is the server refusing on purpose. Its detail is shown only
 *   when it is a reviewed sentence or a closed shape (lib/offerRefusals);
 *   any other refusal reads as the scope's neutral sentence, which names no
 *   cause and gives no advice. describeApiError's conflict and validation
 *   bodies ("Reload to read the current version.", "Clear filters or choose
 *   a supported filter value.") never render here: a reload re-runs every
 *   audited read of this surface, and it has no filters.
 * - Every other failure (a 500, a 503, offline, a 403) is described by the
 *   shared vocabulary (DescribedErrorBody, lib/describeApiError).
 *
 * The sentence loads with the first failure on screen
 * (components/mortgage/OfferFailureSentence): neither the reviewed sentences
 * nor the described vocabulary ride the Offer's natural-load closure, and
 * until that chunk arrives (or if it cannot) the lead phrase stands alone,
 * still buyer-safe.
 *
 * Only a draft-proof refusal (the server asks for a fresh audited draft)
 * offers an explicit Regenerate draft: one user-initiated, audited draft
 * read, never run automatically and never advice to reload the page.
 */

/** A failed governed write: the lead phrase and what caused it. */
export interface OfferWriteFailure {
  lead: string;
  cause: unknown;
}

const FAILURE_SENTENCE_CHUNK = lazyModule(() => import('../components/mortgage/OfferFailureSentence'));

/**
 * The detail of a 409 or 422, the server's deliberate refusals; null for
 * any other failure. A client failure (offline, unreachable, an ended
 * session) never carries either status (lib/apiTransport), so it is still
 * described.
 */
function refusalDetail(cause: unknown): string | null {
  return cause instanceof ApiError && (cause.status === 409 || cause.status === 422) ? cause.message : null;
}

/** The cause sentence: fixed app copy as written, otherwise the failure chunk's sentence. */
export function OfferFailureBody({
  cause,
  subject,
  scope = 'request',
}: {
  cause: unknown;
  subject: string;
  scope?: OfferRefusalScope;
}) {
  const fixed = cause instanceof FixedCopyError;
  const chunk = useLazyModule(FAILURE_SENTENCE_CHUNK, !fixed);
  if (cause instanceof FixedCopyError) return cause.message;
  const Sentence = chunk.module?.OfferFailureSentence;
  return Sentence ? <Sentence cause={cause} refusal={refusalDetail(cause)} subject={subject} scope={scope} /> : null;
}

/**
 * A write failure inside its alert: the lead phrase, then the cause; a
 * draft-proof refusal adds an explicit Regenerate draft (when the route
 * offers it).
 */
export function OfferWriteFailureCopy({
  failure,
  subject = 'the decision',
  scope = 'decision',
  onRegenerate,
  regenerateDisabled = false,
}: {
  failure: OfferWriteFailure;
  subject?: string;
  scope?: OfferRefusalScope;
  onRegenerate?: () => void;
  regenerateDisabled?: boolean;
}) {
  const chunk = useLazyModule(FAILURE_SENTENCE_CHUNK, true);
  const refusal = refusalDetail(failure.cause);
  const regenerate = refusal !== null && chunk.module?.refusalOffersRegenerate(refusal) === true;
  return (
    <>
      {failure.lead}
      {' '}
      <OfferFailureBody cause={failure.cause} subject={subject} scope={scope} />
      {regenerate && onRegenerate && (
        <div className="chip-row mt-2">
          <Button variant="default" size="sm" icon="play" onClick={onRegenerate} disabled={regenerateDisabled}>
            Regenerate draft
          </Button>
        </div>
      )}
    </>
  );
}
