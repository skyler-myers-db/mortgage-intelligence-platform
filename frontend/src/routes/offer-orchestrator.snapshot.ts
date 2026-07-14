import type { ApprovalStatus, Borrower360, OfferRecommendation } from '../types';

export function resolveOfferApprovalStatus(
  local: ApprovalStatus | undefined,
  lifecycleStatus: ApprovalStatus | undefined,
  borrowerStatus: ApprovalStatus | undefined,
): ApprovalStatus | undefined {
  const durable = lifecycleStatus ?? borrowerStatus;
  if (durable === 'approved' || durable === 'rejected' || durable === 'hold') {
    return local ?? durable;
  }
  return local;
}

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
