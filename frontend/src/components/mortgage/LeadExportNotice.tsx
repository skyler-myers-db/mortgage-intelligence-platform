import { Link } from 'react-router';
import { formatCount } from '../../lib/formatters';

/**
 * The export confirmation strip (audit tables-08; D-approval-flow-b). A
 * loaded-rows export of a cohort larger than the rows on screen says it is
 * partial and points at the honest way to work every match: build a campaign
 * (Portfolio Builder with the queue's filters, lead-queue.handoff). With no
 * handoff (Segment Intelligence) the sentence carries no link.
 *
 * The partial strip never retires (useLeadCsvExport), so its link cannot
 * vanish from under the pointer or focus (WCAG 2.2.1), and its link is the
 * handoff held with the export: after a filter change the count and the
 * link still name the cohort that was exported. A plain render
 * helper, not a component: the LeadTable chunk carries no compiler memo
 * cache for one confirmation line.
 */
export function leadExportNotice({
  notice,
  truncatedOf,
  campaignHref,
}: {
  notice: string;
  /** How many borrowers matched when only the loaded ones were exported; else null. */
  truncatedOf: number | null;
  campaignHref: string | null;
}) {
  return (
    <div role="status" aria-live="polite" className="table-success" data-testid="lead-export-notice">
      {notice}
      {truncatedOf !== null && (
        <>
          {` Only the loaded leads were exported. To work all ${formatCount(truncatedOf)}, `}
          {campaignHref
            ? <Link to={campaignHref} data-testid="lead-export-campaign-link">build a campaign</Link>
            : 'build a campaign'}
          .
        </>
      )}
    </div>
  );
}
