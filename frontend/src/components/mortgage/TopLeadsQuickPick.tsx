import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { Icon } from '../Icon';

/**
 * "Today's top leads" — a compact quick-pick for the Borrower 360 and Offer
 * Orchestrator empty states so a no-selection landing is not a dead end. Reads
 * the top ranked leads from the existing leads API and links each to the
 * detail route. Renders nothing until the query resolves, so it never delays
 * the empty-state paint.
 */
export function TopLeadsQuickPick({
  basePath,
  count = 5,
}: {
  basePath: '/borrower-360' | '/offer-orchestrator';
  count?: number;
}) {
  const { data } = useQuery({
    queryKey: queryKeys.leads(['top-quick-pick', String(count)]),
    queryFn: ({ signal }) => api.leads(undefined, signal, undefined, { limit: count }),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const leads = (data ?? []).slice(0, count);
  if (leads.length === 0) return null;
  return (
    <div className="surface mt-grid">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <Icon name="bolt" size={14} className="icon-accent" />
          <div className="h-4">Today's top leads</div>
        </div>
        <Link className="btn btn--ghost btn--sm" to="/lead-queue">
          Open lead queue
          <Icon name="chevright" size={12} />
        </Link>
      </div>
      <div className="surface__body">
        <div className="chip-row">
          {leads.map((lead) => (
            <Link
              key={lead.borrower_id}
              className="chip chip--neutral"
              to={`${basePath}/${lead.borrower_id}`}
              title={`${lead.display_name} · ${lead.city}, ${lead.state} · score ${lead.opportunity_score}`}
            >
              <span className="chip__label">{lead.display_name} · {lead.opportunity_score}</span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
