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
 * over the pattern's allowlisted query, on open; the parent hands the read
 * down (`countOptions`), so this chunk never reaches the initial transport
 * chunk on its own (which would split it). The count is the queue's
 * contactable-only default, and it includes the borrower the pattern came
 * from. A failed count says "Count unavailable" and still offers the links.
 * Prototype: the Primary offer card and the row preview have no cohort
 * action (design_files/Module 0 Prototype.html:1981-1986).
 *
 * The panel opens under its trigger and scrolls itself into the nearest view
 * as it mounts (`revealOpenedPanel`): at 1440x900 the row preview's trigger
 * sits at the Lead Queue table scroller's bottom edge, and the Triage deck's
 * and Borrower 360's at the viewport's, so the panel opened wholly or partly
 * below the fold and the click looked like it did nothing. A mount happens
 * only on the explicit open; the count arriving later changes no height (its
 * line keeps one line box), so nothing scrolls again while it is open.
 */
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import type { LeadSummary } from '../../types';
import { formatCount } from '../../lib/formatters';
import { leadCountQuery, type LeadCountSource } from '../../lib/leadCountQuery';
import { leadPattern } from '../../lib/leadPattern';
import './SimilarCohortPanel.css';

const COUNT_STALE_MS = 60_000;

/**
 * The just-opened panel scrolls into the nearest view of each scroller it is
 * in (the queue table's, then `.main`): no scroll when it is already in view,
 * else the least that shows it, with `.similar-cohort`'s scroll-margin as air
 * under its links. A callback ref, so it runs once, as the panel mounts.
 */
function revealOpenedPanel(element: HTMLDivElement | null): void {
  element?.scrollIntoView?.({ block: 'nearest' });
}

export function SimilarCohortPanel({
  id,
  lead,
  canApprove,
  countOptions,
}: {
  id: string;
  lead: LeadSummary;
  /** The actor may approve: the Triage deck link is theirs only. */
  canApprove: boolean;
  /** The audit-free count read for an allowlisted query (lib/apiClients/leadCount leadCountQueryOptions). */
  countOptions: LeadCountSource;
}) {
  const pattern = leadPattern(lead);
  const count = useQuery({
    ...countOptions(leadCountQuery(pattern.params)),
    staleTime: COUNT_STALE_MS,
    retry: false,
  });
  const search = pattern.params.toString();
  const total = count.data?.total_matching;
  return (
    <div ref={revealOpenedPanel} id={id} className="similar-cohort" role="group" aria-label="More like this" data-testid={`lead-similar-${lead.borrower_id}`}>
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
