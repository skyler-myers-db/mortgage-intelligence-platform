/**
 * Audit explorer (admin-config `#audit`, admin-gated with the route).
 *
 * Audit flow-04 phase 1 / tables-10 (2026-09-21): the filters live in the URL
 * (AdminAuditExplorer.params.ts) so a view can be shared, bookmarked and
 * deep-linked; the controls expose what the API already accepted (actor, a
 * day window, event type, correlation id); rows read as human labels with the
 * raw code kept in a mono chip (AdminAuditExplorer.labels.ts); every audit id
 * links to the explorer opened on that row (`?audit_event_id=`), which the
 * explorer expands and scrolls into view on arrival; and the current page
 * downloads as CSV.
 */
import { lazy, Suspense, useRef, useState, type ComponentType } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { Chip, SurfaceTitle } from '../Primitives';
import { Icon } from '../Icon';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { api, type AuditEventPage } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import { downloadAuditPageCsv } from './AdminAuditExplorer.csv';
import { useScrollDeepLinkedRow } from './AdminAuditExplorer.deepLink';
import { AuditAppliedFilterChips, AuditExplorerFilterForm } from './AdminAuditExplorer.filters';
import { auditEventLabel } from './AdminAuditExplorer.labels';
import {
  auditEventPageQuery,
  auditFiltersKey,
  hasActiveAuditFilters,
  parseAuditExplorerFilters,
  serializeAuditExplorerFilters,
  type AuditExplorerFilters,
} from './AdminAuditExplorer.params';
import { AUDIT_TABLE_CONTEXT, AuditEventTableRow, formatAuditTimestamp } from './AdminAuditExplorer.row';
import { formatCount } from '../../lib/formatters';

interface ErrorBodyProps {
  error: unknown;
  subject: string;
}

const ERROR_BODY_FALLBACK = 'It could not load.';
const ErrorBodyFallback = () => ERROR_BODY_FALLBACK;
type FailureModule = typeof import('../ui/AsyncFailure');

/**
 * The explorer's failure lines in the shared vocabulary (audit states-04):
 * DescribedErrorBody's lazy wrapper (components/ui/DescribedError.tsx),
 * inlined so the admin-config closure does not carry that shared chunk (0.54
 * KiB br against a route gate with none to spare); same chunk, same fallback.
 */
const ErrorBodyLine = lazy<ComponentType<ErrorBodyProps>>(() => (import('../ui/AsyncFailure') as Promise<FailureModule | undefined>).then(
  (module) => ({ default: module?.FailureBody ?? ErrorBodyFallback }),
  () => ({ default: ErrorBodyFallback }),
));

function ErrorBody(props: ErrorBodyProps) {
  return (
    <Suspense fallback={ERROR_BODY_FALLBACK}>
      <ErrorBodyLine {...props} />
    </Suspense>
  );
}

interface AuditRollupRow {
  bucket_start: string;
  event_type: string;
  event_count: number;
}

interface AuditCopyState {
  value: string;
  message: string;
  failed: boolean;
}

const AUDIT_PAGE_SIZE = 25;

export function AdminAuditExplorer() {
  const location = useLocation();
  const navigate = useNavigate();
  const explorerRef = useRef<HTMLDivElement>(null);
  const applyButtonRef = useRef<HTMLButtonElement>(null);
  const searchParams = new URLSearchParams(location.search);
  const applied = parseAuditExplorerFilters(searchParams);
  const appliedKey = auditFiltersKey(applied);
  // Cursor pages and the expanded row belong to one applied filter set; a
  // new set (Apply, a chip removal, a deep link, Back) starts on page 1.
  const [cursorState, setCursorState] = useState<{ key: string; cursors: Array<string | null> }>(
    { key: appliedKey, cursors: [null] },
  );
  const [expandedState, setExpandedState] = useState<{ key: string; id: string | null } | null>(null);
  const [copyState, setCopyState] = useState<AuditCopyState | null>(null);

  const pageCursors = cursorState.key === appliedKey ? cursorState.cursors : [null];
  const page = pageCursors.length - 1;
  const pageCursor = pageCursors[page] ?? null;
  const filtersActive = hasActiveAuditFilters(applied);
  // A deep link opens its row: expanded until the user collapses it.
  const expandedEventId = expandedState?.key === appliedKey
    ? expandedState.id
    : applied.eventId || null;

  const applyFilters = (next: AuditExplorerFilters) => {
    const params = serializeAuditExplorerFilters(next, searchParams).toString();
    navigate(
      { search: params ? `?${params}` : '', hash: location.hash },
      { preventScrollReset: true },
    );
  };

  const {
    data: pageRows,
    warmingUp,
    error: errorObj,
    isFetching,
  } = useWarmingUpRetry<AuditEventPage>(
    (signal) => api.auditEventPage(AUDIT_PAGE_SIZE, signal, {
      ...auditEventPageQuery(applied),
      cursor: pageCursor,
    }),
    {
      queryKey: queryKeys.auditEvents(['explorer', appliedKey, pageCursor]),
      keepPreviousData: false,
    },
  );
  const events = pageRows?.items ?? (pageRows === null ? null : []);
  useScrollDeepLinkedRow(explorerRef, applied.eventId, events);
  const hasNextPage = Boolean(pageRows?.next_cursor);
  const firstShownRow = events?.length ? page * AUDIT_PAGE_SIZE + 1 : 0;
  const lastShownRow = page * AUDIT_PAGE_SIZE + (events?.length ?? 0);
  const updating = pageRows !== null && (isFetching || Boolean(warmingUp));
  const statusLabel = updating
    ? warmingUp
      ? `${warmingUp.label} (${warmingUp.attempt}/${warmingUp.maxAttempts})`
      : 'Updating audit rows'
    : '';
  // Failures read in the shared vocabulary, never the transport message
  // (audit states-04).
  const error = errorObj;

  const {
    data: rollups,
    warmingUp: rollupsWarming,
    error: rollupsErrorObj,
  } = useWarmingUpRetry<AuditRollupRow[]>(
    (signal) => api.auditRollups('week', signal),
    { queryKey: queryKeys.auditRollups('week') },
  );
  const rollupsError = rollupsErrorObj;

  const goToPage = (cursors: Array<string | null>) => {
    setExpandedState({ key: appliedKey, id: null });
    setCursorState({ key: appliedKey, cursors });
  };
  const copyValue = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopyState({ value, message: `${label} copied`, failed: false });
    } catch {
      setCopyState({
        value,
        message: `Could not copy ${label.toLowerCase()}`,
        failed: true,
      });
    }
  };

  return (
    <div
      ref={explorerRef}
      className="surface mt-grid"
      id="audit"
      tabIndex={-1}
      aria-labelledby="audit-explorer-title"
    >
      <div className="surface__hdr surface__hdr--split">
        <div>
          <SurfaceTitle id="audit-explorer-title">Audit explorer</SurfaceTitle>
          <div className="muted fs-12">
            Filter the Lakebase ledger by actor, day, event type, entity or correlation id.
          </div>
        </div>
        <div className="chip-row">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void copyValue(AUDIT_TABLE_CONTEXT, 'Table context')}
            aria-label="Copy Lakebase audit table context"
            title="Copy Lakebase audit table name"
          >
            <Icon name="doc" size={12} />
            {copyState?.value === AUDIT_TABLE_CONTEXT && !copyState.failed
              ? 'Copied table context'
              : 'Copy table context'}
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => events && downloadAuditPageCsv(events, page)}
            disabled={!events?.length || updating}
            aria-label={`Download page ${page + 1} of the audit explorer as CSV`}
            title="Download the rows on this page as CSV"
          >
            <Icon name="export" size={12} />
            Page CSV
          </button>
          <Chip variant={error ? 'warning' : filtersActive ? 'success' : 'neutral'}>
            {error
              ? 'reconnecting'
              : updating
                ? 'updating'
                : filtersActive
                  ? events === null
                    ? 'filtering…'
                    : `page ${page + 1} · ${events.length} rows`
                  : `page ${page + 1} · ${events?.length ?? 0} rows`}
          </Chip>
        </div>
      </div>
      <div className="surface__body">
        {copyState && (
          <div
            role={copyState.failed ? 'alert' : 'status'}
            aria-live={copyState.failed ? 'assertive' : 'polite'}
            className="muted fs-12"
          >
            {copyState.message}
          </div>
        )}
        <AuditExplorerFilterForm applied={applied} onApply={applyFilters} applyButtonRef={applyButtonRef} />
        <AuditAppliedFilterChips
          applied={applied}
          emptiedFocusRef={applyButtonRef}
          summary={`Showing rows ${firstShownRow}-${lastShownRow} that match the applied filters.`}
          onRemove={(key) => applyFilters({ ...applied, [key]: '' })}
        />
        {warmingUp && events === null && (
          <div className="mt-3">
            <WarmingUpBlock state={warmingUp} title="Audit explorer loading" compact />
          </div>
        )}
        {error && !warmingUp && (
          <div className="muted body fs-12 mt-3">
            Audit explorer unavailable: <ErrorBody error={error} subject="the audit explorer" />
          </div>
        )}
        <div className="admin-rollups mt-3">
          <div className="h-5">Recorded audit events by week</div>
          {rollupsWarming && (
            <WarmingUpBlock state={rollupsWarming} title="Audit rollups loading" compact />
          )}
          {rollupsError && !rollupsWarming && (
            <div className="muted fs-12">
              Audit rollups unavailable: <ErrorBody error={rollupsError} subject="the audit rollups" />
            </div>
          )}
          {!rollupsWarming && !rollupsError && (
            <div className="admin-rollups__grid">
              {(rollups ?? []).slice(0, 6).map((row) => (
                <div key={`${row.bucket_start}-${row.event_type}`} className="admin-rollup">
                  <span className="fs-12">
                    {auditEventLabel({ event_type: row.event_type, action: row.event_type })}
                  </span>
                  <span className="mono muted fs-11">{row.event_type}</span>
                  <strong>{formatCount(row.event_count)}</strong>
                  <span className="muted fs-11">{formatAuditTimestamp(row.bucket_start)}</span>
                </div>
              ))}
              {rollups?.length === 0 && (
                <div className="muted fs-12">
                  No workflow events in the current rollup window.
                </div>
              )}
            </div>
          )}
        </div>
        {events !== null && !error && (
          <div
            className={`admin-audit-list stable-refresh-region stable-refresh-region--table mt-3 ${updating ? 'is-updating' : ''}`}
            aria-label="Audit event table"
            aria-busy={updating}
            data-status={statusLabel}
          >
            {events.length > 0 && (
              <table className="tbl tbl--static" aria-label="Audit events">
                <thead>
                  <tr>
                    <th scope="col" aria-label="Event details" />
                    <th scope="col">Event</th>
                    <th scope="col">Entity</th>
                    <th scope="col">Actor</th>
                    <th scope="col">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <AuditEventTableRow
                      key={event.event_id}
                      event={event}
                      expanded={expandedEventId === event.event_id}
                      copiedValue={copyState?.failed ? null : copyState?.value ?? null}
                      onToggle={() => setExpandedState({
                        key: appliedKey,
                        id: expandedEventId === event.event_id ? null : event.event_id,
                      })}
                      onCopy={(value, label) => void copyValue(value, label)}
                    />
                  ))}
                </tbody>
              </table>
            )}
            {events.length === 0 && (
              <div className="muted fs-12">
                {page > 0
                  ? 'No more audit rows match these filters on this page.'
                  : applied.eventId
                    ? 'No audit event has this id. It may belong to another environment.'
                    : 'No audit rows match these filters.'}
              </div>
            )}
            <div className="section-actions admin-audit-pagination" aria-label="Audit result pages">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={page === 0 || updating}
                onClick={() => goToPage(pageCursors.slice(0, -1))}
              >
                Previous
              </button>
              <span className="muted mono fs-11" aria-live="polite">Page {page + 1}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={!hasNextPage || updating}
                onClick={() => {
                  if (!pageRows?.next_cursor) return;
                  goToPage([...pageCursors, pageRows.next_cursor]);
                }}
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
