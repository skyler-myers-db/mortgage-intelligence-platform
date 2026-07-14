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

/** Bind a generated draft to the same normalized gold refresh as its source payloads. */
export function draftProofMatchesSnapshot(
  borrower: Borrower360 | null,
  recommendation: OfferRecommendation | null,
  draftSourceRefreshedAt: string | null | undefined,
): boolean {
  if (!borrower || !recommendation) return false;
  const borrowerVersion = borrower.source_refreshed_at?.trim();
  const recommendationVersion = recommendation.source_refreshed_at?.trim();
  const draftVersion = draftSourceRefreshedAt?.trim();
  return Boolean(
    borrowerVersion
      && recommendationVersion
      && draftVersion
      && borrowerVersion === draftVersion
      && recommendationVersion === draftVersion,
  );
}
