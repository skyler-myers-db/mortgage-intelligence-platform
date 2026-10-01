import { Link } from 'react-router';
import { preloadRouteForPath } from '../../lib/routePreloaders';
import type { LeadTableCampaignHandoff } from './LeadTable.types';

/**
 * The scope line's campaign handoff (audit tables-07 / tables-02,
 * D-approval-flow-a3; deviation:bulk-scope-handoff): a link to Portfolio
 * Builder with the queue's filters, and the ones it cannot carry. It rides
 * the lazy bulk chunk (re-exported from LeadBulkApproveReview.tsx) and
 * mounts in LeadTableBulkActions' `campaignHandoff` slot; the scope
 * sentence itself stays in the toolbar. The link preloads that route's
 * code only; it reads nothing.
 */
function preloadPortfolioBuilder(): void {
  preloadRouteForPath('/portfolio-builder');
}

export function LeadBulkCampaignHandoff({ handoff }: { handoff: LeadTableCampaignHandoff }) {
  const partial = handoff.notCarried.length > 0;
  return (
    <>
      <Link
        to={handoff.href}
        className="btn btn--ghost btn--sm"
        onPointerEnter={preloadPortfolioBuilder}
        onFocus={preloadPortfolioBuilder}
        data-testid="lead-bulk-campaign-handoff"
      >
        {partial ? 'Build a campaign from the filters that carry over' : 'Build a campaign from these filters'}
      </Link>
      {partial && (
        <span className="muted" data-testid="lead-bulk-not-carried">
          Not carried: {handoff.notCarried.join(', ')}
        </span>
      )}
    </>
  );
}
