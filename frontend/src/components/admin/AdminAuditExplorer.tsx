/**
 * Audit explorer (/audit-ledger `#audit`, gated with the route to administrators
 * and the read-only Auditor role, D-audit-reads-c3).
 *
 * Audit flow-04 phase 1 / tables-10 (2026-09-21): the filters live in the URL
 * (AdminAuditExplorer.params.ts) so a view can be shared, bookmarked and
 * deep-linked; the controls expose what the API already accepted (actor, a
 * day window, event type, correlation id); rows read as human labels with the
 * raw code kept in a mono chip (AdminAuditExplorer.labels.ts); every audit id
 * links to the explorer opened on that row (`?audit_event_id=`), which the
 * explorer expands and scrolls into view on arrival; and the current page
 * downloads as CSV.
 *
 * W5d (tables-10; deviation:audit-explorer-controls): the Action menu and the
 * menus' counts come from the facets, read only on an explicit menu open;
 * 'Exclude my ledger reads', a per-viewer, server-side filter kept out of the
 * URL (a shared link must not silently mean 'hide the recipient's reads');
 * the page CSV, for administrators only, downloads only after its
 * AUDIT_EXPORT receipt (AdminAuditExplorer.export.ts, loaded on click); and
 * Export PDF behind a PRINT_EXPORT receipt for every ledger reader.
 */
import { lazy, Suspense, useRef, useState, type ComponentType } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { Chip, SurfaceTitle } from '../Primitives';
import { Icon } from '../Icon';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { PrintProvenance } from '../print/PrintProvenance';
import { toastReceiptFailure } from '../print/receiptFailureToast';
import { usePrintReceipt } from '../print/usePrintReceipt';
import type { PrintReceiptRequest } from '../../lib/apiClients/printReceipt';
import { api, type AuditEventPage, type AuditEventRow } from '../../lib/api';
import {
  auditEventPage,
  auditQueryFilters,
  createAuditExportReceipt,
  type AuditExplorerQuery,
} from '../../lib/apiClients/auditExplorer';
import { queryKeys } from '../../lib/queryKeys';
import { toast } from '../../lib/toast';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import { AuditFacetsProvider } from './AdminAuditExplorer.facets';
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
import './AdminAuditExplorer.css';

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
 * inlined so the audit-ledger closure does not carry that shared chunk (0.54
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

/**
 * The audited page CSV (administrators only): the file and its hashes are
 * built in a click-time chunk; the download waits for the AUDIT_EXPORT row.
 * Module scope on purpose: an import() inside the component body is a React
 * Compiler bailout (BuildHIR cannot lower Import expressions).
 */
async function exportAuditPageCsv(
  events: readonly AuditEventRow[],
  page: number,
  filters: Record<string, string>,
  retry: () => void,
): Promise<void> {
  try {
    const csvModule = await import('./AdminAuditExplorer.export');
    const prepared = await csvModule.prepareAuditPageCsv(events, page, filters);
    const receipt = await createAuditExportReceipt(prepared.declaration);
    csvModule.downloadPreparedCsv(prepared);
    toast.success('Audit page exported', { auditEventId: receipt.audit_event_id });
  } catch (error: unknown) {
    toastReceiptFailure('Audit page not exported', error, 'the audit export receipt', retry);
  }
}

export function AdminAuditExplorer() {
  const location = useLocation();
  const navigate = useNavigate();
  const explorerRef = useRef<HTMLDivElement>(null);
  const applyButtonRef = useRef<HTMLButtonElement>(null);
  const searchParams = new URLSearchParams(location.search);
  const applied = parseAuditExplorerFilters(searchParams);
  // 'Exclude my ledger reads': per viewer, default off, never in the URL. It
  // is part of the page's identity (query and cursor-reset key).
  const [excludeOwnReads, setExcludeOwnReads] = useState(false);
  const appliedKey = `${auditFiltersKey(applied)}${excludeOwnReads ? '|exclude-own-ledger-reads' : ''}`;
  const explorerQuery: AuditExplorerQuery = {
    ...auditEventPageQuery(applied),
    exclude_own_ledger_reads: excludeOwnReads,
  };
  const { canAccessAdmin } = useApp();
  const [csvPending, setCsvPending] = useState(false);
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
    (signal) => auditEventPage(AUDIT_PAGE_SIZE, signal, { ...explorerQuery, cursor: pageCursor }),
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

  // The query of the page on screen, as the receipts' fingerprinted filters
  // (only the set parameters, as the page request sends them).
  const receiptFilters = auditQueryFilters(explorerQuery);
  const printRequest: PrintReceiptRequest | null = events?.length
    ? { surface: 'audit_ledger', filters: receiptFilters, row_count: events.length }
    : null;
  // Export PDF (responsive-08; deviation:print-export): any ledger reader
  // prints the page already rendered, behind a PRINT_EXPORT receipt. The
  // receipt belongs to THIS page: the page index and the event ids on screen
  // are its client-only identity (never sent), so the next page of the same
  // query, even one of the same size, prints as unaudited until its own
  // Export PDF, and a receipt resolving after Next prints nothing.
  const print = usePrintReceipt(
    printRequest,
    `${page}|${(events ?? []).map((event) => event.event_id).join(',')}`,
  );
  const exportCsv = () => {
    if (!events?.length) return;
    setCsvPending(true);
    void exportAuditPageCsv(events, page, receiptFilters, exportCsv).finally(() => setCsvPending(false));
  };

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
      <div className="surface__hdr surface__hdr--split surface__hdr--audit">
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
          <label className="audit-exclude-own">
            <input
              type="checkbox"
              checked={excludeOwnReads}
              onChange={(event) => setExcludeOwnReads(event.target.checked)}
            />
            Exclude my ledger reads
          </label>
          {canAccessAdmin && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={exportCsv}
              disabled={!events?.length || updating || csvPending}
              aria-busy={csvPending || undefined}
              aria-label={`Download page ${page + 1} of the audit explorer as CSV`}
              title="Download the rows on this page as CSV"
            >
              <Icon name="export" size={12} />
              Page CSV
            </button>
          )}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={print.run}
            disabled={!events?.length || updating || print.pending}
            aria-busy={print.pending || undefined}
            data-testid="audit-export-pdf"
          >
            <Icon name="export" size={12} />
            Export PDF
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
        <AuditFacetsProvider since={applied.since} until={applied.until} eventType={applied.eventType}>
          <AuditExplorerFilterForm applied={applied} onApply={applyFilters} applyButtonRef={applyButtonRef} />
        </AuditFacetsProvider>
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
          <PrintProvenance
            subject={`Audit ledger page ${page + 1} · ${formatCount(events.length)} rows`}
            receipt={print.receipt}
          />
        )}
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
