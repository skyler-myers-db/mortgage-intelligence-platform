/**
 * "More like this" (wow-power-2, W5e item 4b). deviation:more-like-this
 *
 * One borrower's reviewed pattern (lib/leadPattern: primary segment,
 * recommended product, state, lender relationship, score band) as chips, the
 * true number of borrowers it matches, and LINKS only: "Open in Lead Queue"
 * and, for an approver, "Open as Triage deck" (the `?mode=triage` deep link,
 * D-approval-flow-a2). No approve, assign or batch control and no write
 * (D-approval-flow-a3: one decision never becomes thirty; each approval stays
 * individual, with its own draft and audit row).
 *
 * Its own lazy chunk, loaded on the explicit "More like this" click (never on
 * a row expand or hover). The one read is the audit-free GET /api/leads/count
 * over the pattern's allowlisted query, on open. The count is the queue's
 * contactable-only default, and it includes the borrower the pattern came
 * from. A failed count says "Count unavailable" and still offers the links.
 * Prototype: the Primary offer card and the row preview have no cohort
 * action (design_files/Module 0 Prototype.html:1981-1986).
 */
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import type { LeadSummary } from '../../types';
import { fetchLeadCount, leadCountQuery } from '../../lib/apiClients/leadCount';
import { formatCount } from '../../lib/formatters';
import { leadPattern } from '../../lib/leadPattern';
import { queryKeys } from '../../lib/queryKeys';
import './SimilarCohortPanel.css';

const COUNT_STALE_MS = 60_000;

export function SimilarCohortPanel({
  id,
  lead,
  canApprove,
}: {
  id: string;
  lead: LeadSummary;
  /** The actor may approve: the Triage deck link is theirs only. */
  canApprove: boolean;
}) {
  const pattern = leadPattern(lead);
  const query = leadCountQuery(pattern.params);
  const count = useQuery({
    queryKey: queryKeys.leadCount([query]),
    queryFn: ({ signal }) => fetchLeadCount(pattern.params, signal),
    staleTime: COUNT_STALE_MS,
    retry: false,
  });
  const search = pattern.params.toString();
  const total = count.data?.total_matching;
  return (
    <div id={id} className="similar-cohort" role="group" aria-label="More like this" data-testid={`lead-similar-${lead.borrower_id}`}>
      <div className="chip-row">
        {pattern.chips.map((chip) => (
          <span key={chip.label} className="filter similar-cohort__chip">
            <span className="filter__label">{chip.label}</span>
            <span className="filter__value">{chip.value}</span>
          </span>
        ))}
      </div>
      <p className="similar-cohort__count" role="status" aria-busy={count.isPending || undefined}>
        {count.isError
          ? 'Count unavailable'
          : total === undefined
            ? ''
            : `${formatCount(total)} ${total === 1 ? 'borrower matches' : 'borrowers match'} this pattern`}
      </p>
      <p className="similar-cohort__note">Contactable borrowers only, the queue&apos;s default.</p>
      <div className="chip-row">
        <Link className="btn btn--default btn--sm" to={`/lead-queue?${search}`} data-testid="lead-similar-open-queue">
          Open in Lead Queue
        </Link>
        {canApprove && (
          <Link className="btn btn--ghost btn--sm" to={`/lead-queue?${search}&mode=triage`} data-testid="lead-similar-open-triage">
            Open as Triage deck
          </Link>
        )}
      </div>
    </div>
  );
}
