import { DescribedErrorBody } from '../ui/DescribedError';
import { offerRefusalSentence, refusalOffersRegenerate, type OfferRefusalScope } from '../../lib/offerRefusals';

/**
 * The Offer Orchestrator's failure sentence (2026-09-21 audit states-04): a
 * server refusal (a 409 or 422, passed in as its detail) reads as a reviewed
 * sentence or the scope's neutral one (lib/offerRefusals); any other failure
 * is described by the shared vocabulary (DescribedErrorBody). Never the raw
 * message, and never describeApiError's reload or filter advice for a
 * refusal.
 *
 * Its own chunk: routes/offer-orchestrator.errors loads it with the first
 * failure on screen, so neither the reviewed sentences nor the described
 * vocabulary ride the Offer's natural load.
 */
export function OfferFailureSentence({
  cause,
  refusal,
  subject,
  scope,
}: {
  cause: unknown;
  /** The detail of a 409 or 422; null for any other failure. */
  refusal: string | null;
  subject: string;
  scope: OfferRefusalScope;
}) {
  if (refusal !== null) return offerRefusalSentence(refusal, scope);
  return <DescribedErrorBody error={cause} subject={subject} />;
}

/** Whether a refusal asks for a fresh audited draft (the alert's Regenerate draft). */
export { refusalOffersRegenerate };
