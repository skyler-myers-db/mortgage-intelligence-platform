import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { GenieRefusalReason } from '../../types';
import { refusalReportsApi, type RefusalReportItem } from '../../lib/apiClients/refusalReports';
import { queryKeys } from '../../lib/queryKeys';
import { Chip, SurfaceTitle } from '../Primitives';
import { GENIE_REFUSAL_FAMILIES } from '../mortgage/genieRefusal';
import { RefusalReportRow } from './RefusalReportRow';

/**
 * RefusalReportsPanel — "This was legitimate" reports for administrators
 * and auditors on /audit-ledger (D-audit-reads-d, audit genie-05).
 *
 * Loaded only on an explicit "Show refusal reports" (routes/audit-ledger):
 * every served page is itself recorded server-side as one VIEW_AUDIT_LEDGER
 * row, so a page is read only when the panel opens, the family filter
 * changes, Load more asks for the next page, or Retry. Never on hover, a
 * prefetch, a poll, window focus or reconnect (`meta.auditedPages` keeps
 * health recovery off it too). The list carries report metadata only; one
 * consented question is read per explicit "Show question" click
 * (RefusalReportRow), which the server audits fail-closed.
 *
 * Prototype BEM only: `.surface` / `.surface__hdr` / `.tbl` / `.chip`
 * (design_files/index.html:595 `.tbl`; the ledger route itself is the
 * declared departure from the single-screen prototype, design_files/Module 0
 * Prototype.html:1188-1204). deviation:refusal-reports-panel
 */

const FAMILIES = Object.keys(GENIE_REFUSAL_FAMILIES) as GenieRefusalReason[];

function RefusalReportsPage({
  family,
  cursor,
  onLoadMore,
}: {
  family: GenieRefusalReason | null;
  cursor: string | null;
  /** Present on the last loaded page only. */
  onLoadMore: ((cursor: string) => void) | null;
}) {
  const page = useQuery({
    queryKey: queryKeys.refusalReports(family, cursor),
    queryFn: ({ signal }) => refusalReportsApi.list({ family, cursor }, signal),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    meta: { auditedPages: true },
  });
  if (page.isPending) {
    return (
      <tr>
        <td colSpan={6} className="muted fs-12" aria-busy="true">Loading refusal reports…</td>
      </tr>
    );
  }
  if (page.isError) {
    return (
      <tr>
        <td colSpan={6} className="fs-12">
          <span role="alert">The refusal reports could not be read.</span>{' '}
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void page.refetch()}>
            Retry
          </button>
        </td>
      </tr>
    );
  }
  const nextCursor = page.data.next_cursor;
  return (
    <>
      {page.data.items.map((item: RefusalReportItem) => (
        <RefusalReportRow key={item.report_id} item={item} />
      ))}
      {cursor === null && page.data.items.length === 0 && (
        <tr>
          <td colSpan={6} className="muted fs-12">No refusal reports in the last 90 days.</td>
        </tr>
      )}
      {onLoadMore && nextCursor && (
        <tr>
          <td colSpan={6}>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onLoadMore(nextCursor)}>
              Load more
            </button>
          </td>
        </tr>
      )}
    </>
  );
}

function FamilyCounts({ family }: { family: GenieRefusalReason | null }) {
  // The first page's window counts; reads the cache only (enabled: false),
  // so it never issues a request of its own.
  const first = useQuery({
    queryKey: queryKeys.refusalReports(family, null),
    queryFn: ({ signal }) => refusalReportsApi.list({ family, cursor: null }, signal),
    enabled: false,
  });
  const counts = first.data?.family_counts ?? [];
  if (counts.length === 0) return null;
  return (
    <div className="chip-row" aria-label="Reports per refusal family">
      {counts.map((row) => (
        <Chip key={row.refusal_reason} variant="neutral">
          {GENIE_REFUSAL_FAMILIES[row.refusal_reason].title} · {row.count}
        </Chip>
      ))}
    </div>
  );
}

export function RefusalReportsPanel() {
  const [family, setFamily] = useState<GenieRefusalReason | null>(null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  return (
    <section className="surface mt-grid" aria-labelledby="refusal-reports-title" data-testid="refusal-reports-panel">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <SurfaceTitle id="refusal-reports-title">Refusal reports</SurfaceTitle>
          <div className="muted fs-12">
            Governed Genie refusals a lender marked as legitimate, from the last 90 days. Reading this list is recorded.
          </div>
        </div>
        <label className="filter-row__group">
          <span className="field__label">FAMILY</span>
          <select
            className="form-input"
            value={family ?? ''}
            onChange={(event) => {
              setFamily((event.target.value || null) as GenieRefusalReason | null);
              setCursors([null]);
            }}
            data-testid="refusal-reports-family"
          >
            <option value="">All families</option>
            {FAMILIES.map((code) => (
              <option key={code} value={code}>{GENIE_REFUSAL_FAMILIES[code].title}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="surface__body">
        <FamilyCounts family={family} />
        <table className="tbl tbl--static mt-3" aria-label="Refusal reports">
          <thead>
            <tr>
              <th scope="col">Reported</th>
              <th scope="col">Family</th>
              <th scope="col">Reporter</th>
              <th scope="col">Conversation · message</th>
              <th scope="col">Audit event</th>
              <th scope="col">Question</th>
            </tr>
          </thead>
          <tbody>
            {cursors.map((cursor, index) => (
              <RefusalReportsPage
                key={cursor ?? 'first'}
                family={family}
                cursor={cursor}
                onLoadMore={
                  index === cursors.length - 1
                    ? (next) => setCursors((current) => [...current, next])
                    : null
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default RefusalReportsPanel;
