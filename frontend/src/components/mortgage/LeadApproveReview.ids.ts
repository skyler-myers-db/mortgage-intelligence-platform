/**
 * Ids the approve review and the table share (the review itself is a lazy
 * chunk; these stay with the table so focus logic never waits on it).
 */

export function leadApproveReviewId(borrowerId: string): string {
  return `lead-approve-review-${borrowerId}`;
}

/** True when `element` sits inside the open review for `borrowerId`. */
export function isInsideLeadApproveReview(element: Element | null, borrowerId: string): boolean {
  return element?.closest(`[id="${leadApproveReviewId(borrowerId)}"]`) != null;
}
