import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { Icon } from '../components/Icon';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { PortfolioPreview } from '../types';
import { formatCount } from '../lib/formatters';

/**
 * Home's "Approval queue" banner — prototype `.approval` BEM, unchanged.
 *
 * Addressable-vs-contactable reconciliation (2026-09-21 audit, flow-v1). The
 * banner quoted the whole-book refinance-economics screen
 * (`high_intent_leads` from the `marketing_eligibility: 'Any'` preview) and
 * its button opened `/lead-queue?segment=itm`, which applies the
 * contact-eligibility predicate by default ("Eligible only"). On live gold
 * those were 74,335 and 3,217 — about 23x apart — so the first click of the
 * demo landed on a number that disagreed with the one just read. Same single
 * root cause as the segment cards and the map's state tiles: every headline
 * count is the full population, every Lead Queue is the contactable subset,
 * and nothing stated the relationship. Both numbers were correct.
 *
 * So the banner states both, in the SegmentCard idiom ("N contactable of M
 * ..."), and it never un-filters the queue: contactability is governed.
 *
 *   M = `screenCount` — the SAME preview value the "Refi economics screen" KPI
 *       card above it renders, so the two cannot disagree.
 *   N = the same `/api/portfolio/preview` aggregate under 'Eligible only':
 *       SUM(in_the_money) with `eligible_sql_predicate()` applied. One
 *       statement plus one predicate, so N <= M by construction.
 *
 * Why N is the number the queue's "total matching filters" footer reports for
 * this link — pinned by tests/unit/test_home_banner_contactable_queue_parity.py:
 * the queue's default criteria ARE these criteria; it compiles them with the
 * same `build_preview_predicates`; with them it counts `gold.borrower_360`
 * (not the score-floored `gold.lead_population`), which the headline view the
 * preview aggregates projects unfiltered; and `in_the_money` is exactly what
 * puts 'itm' in `segment_codes`. What is left is timing only: two requests,
 * each behind its own short TTL cache.
 *
 * NOT `GET /api/leads`: that read writes a VIEW_LEADS audit row per call, and
 * a banner must never write audit rows as a side effect of rendering. The
 * preview endpoint is a pure aggregate with no audit write.
 */

export const APPROVAL_QUEUE_STATE_LABEL = 'current lifecycle state';
export const APPROVAL_QUEUE_HREF = '/lead-queue?segment=itm';
/** The Lead Queue's default contactability, which the banner's link inherits. */
export const HOME_CONTACTABLE_PREVIEW_CRITERIA = { marketing_eligibility: 'Eligible only' } as const;

export function requestHomeContactablePreview(signal?: AbortSignal) {
  return api.portfolioPreview(HOME_CONTACTABLE_PREVIEW_CRITERIA, signal);
}

interface ApprovalQueueBannerProps {
  /** Whole-book refinance-economics screen (M); null while Home's preview loads. */
  screenCount: number | null;
  approvedCount: number;
  inOutreachCount: number;
}

export function ApprovalQueueBanner({ screenCount, approvedCount, inOutreachCount }: ApprovalQueueBannerProps) {
  // Additive fetch, like the last-login summary: Home's KPI row owns the
  // warming / degraded story, so a warming or failed count here never stacks a
  // second callout — the banner just says what it still knows to be true.
  const { data: contactablePreview } = useWarmingUpRetry<PortfolioPreview>(
    requestHomeContactablePreview,
    ['home', 'contactable'],
    { queryKey: queryKeys.portfolioPreview(['home', 'contactable']) },
  );
  const reported = contactablePreview?.high_intent_leads;
  // Two requests can straddle a gold refresh; clamp so the sentence can never
  // claim more contactable borrowers than pass the screen.
  const contactable =
    typeof reported === 'number' && Number.isFinite(reported) && screenCount !== null
      ? Math.min(reported, screenCount)
      : null;
  // Only when there is a gap to state — "3,217 contactable of 3,217" is a
  // tautology that reads as a bug (the SegmentCard lesson, 2026-08-11).
  const showsReconcile = contactable !== null && screenCount !== null && contactable < screenCount;
  const lifecycle = `${formatCount(approvedCount)} approved and ${formatCount(inOutreachCount)} in outreach in ${APPROVAL_QUEUE_STATE_LABEL}.`;

  let body: ReactNode;
  if (screenCount === null) {
    body = 'Borrowers passing the refinance-economics screen are ready for loan-officer review.';
  } else if (showsReconcile) {
    body = (
      <>
        <span className="num" data-testid="approval-queue-contactable">{formatCount(contactable)}</span>
        {' '}contactable of{' '}
        <span className="num" data-testid="approval-queue-screen">{formatCount(screenCount)}</span>
        {' '}borrowers passing the refinance-economics screen. {lifecycle}
      </>
    );
  } else if (contactable !== null) {
    body = (
      <>
        <span className="num" data-testid="approval-queue-screen">{formatCount(screenCount)}</span>
        {' '}borrowers pass the refinance-economics screen. {lifecycle}
      </>
    );
  } else {
    // The contactable count is not known (still loading, or its request
    // failed). Stating M alone beside a button into the contactable queue is
    // the defect, so say what the queue is instead of implying its size.
    body = (
      <>
        <span className="num" data-testid="approval-queue-screen">{formatCount(screenCount)}</span>
        {' '}borrowers pass the refinance-economics screen across the whole book; the review queue
        lists only the contactable subset. {lifecycle}
      </>
    );
  }

  return (
    <div role="region" aria-label="Approval queue" className="approval">
      <div className="approval__ico"><Icon name="shield" size={16} /></div>
      <div className="approval__body">
        <div className="approval__title">Approval queue</div>
        <div className="approval__sub">{body}</div>
      </div>
      {/* Secondary: Home carries exactly one primary action, "Review today's
          top leads" (2026-09-21 audit flow-05). Spacing comes from Home's
          grid gap, so the banner no longer carries its own top margin. */}
      <Link to={APPROVAL_QUEUE_HREF} className="btn btn--sm">
        Open review queue
        <Icon name="chevright" size={13} />
      </Link>
    </div>
  );
}
