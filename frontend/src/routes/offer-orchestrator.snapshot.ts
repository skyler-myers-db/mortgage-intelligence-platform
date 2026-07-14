import type { Borrower360, OfferRecommendation } from '../types';

/** Keep independently fetched borrower and offer payloads on one gold refresh. */
export function offerSnapshotMatches(
  borrower: Borrower360,
  recommendation: OfferRecommendation,
): boolean {
  const borrowerVersion = borrower.source_refreshed_at?.trim();
  const recommendationVersion = recommendation.source_refreshed_at?.trim();
  return Boolean(
    borrowerVersion
      && recommendationVersion
      && borrowerVersion === recommendationVersion,
  );
}
