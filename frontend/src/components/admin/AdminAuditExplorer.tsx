import { Fragment, useState } from 'react';
import type { FormEvent } from 'react';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { api, type AuditEventRow } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp } from '../../lib/time';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';

interface AuditRollupRow {
  bucket_start: string;
  event_type: string;
  event_count: number;
}

type AdminAuditEvent = AuditEventRow;

interface AuditCopyState {
  value: string;
  message: string;
  failed: boolean;
}

const AUDIT_TABLE_CONTEXT = 'mip_app.action_audit';

export function AdminAuditExplorer() {
  const [entityDraft, setEntityDraft] = useState<string>('');
  const [actionDraft, setActionDraft] = useState<string>('');
  const [eventTypeDraft, setEventTypeDraft] = useState<string>('');
  const [entityFilter, setEntityFilter] = useState<string>('');
  const [actionFilter, setActionFilter] = useState<string>('');
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('');
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<AuditCopyState | null>(null);

  const entityValue = entityFilter.trim();
  const entityIsBorrower = /^B-[A-Z0-9]+$/i.test(entityValue);
  const filtersActive = Boolean(
    entityFilter.trim() || actionFilter.trim() || eventTypeFilter.trim(),
  );

  const {
    data: events,
    warmingUp,
    error: errorObj,
    isFetching,
    isPlaceholderData,
  } = useWarmingUpRetry<AdminAuditEvent[]>(
    (signal) => api.auditEvents(25, signal, {
      entity_id: entityValue && !entityIsBorrower ? entityValue : null,
      borrower_id: entityValue && entityIsBorrower ? entityValue : null,
      action: actionFilter.trim() || null,
      event_type: eventTypeFilter.trim() || null,
    }),
    [entityValue, entityIsBorrower, actionFilter, eventTypeFilter],
    {
      queryKey: queryKeys.auditEvents([
        'explorer',
        entityValue,
        entityIsBorrower,
        actionFilter,
        eventTypeFilter,
      ]),
      keepPreviousData: true,
    },
  );
  const updating = events !== null && (
    isFetching || isPlaceholderData || Boolean(warmingUp)
  );
  const statusLabel = updating
    ? warmingUp
      ? `${warmingUp.label} (${warmingUp.attempt}/${warmingUp.maxAttempts})`
      : 'Updating audit rows'
    : '';
  const error = errorObj
    ? errorObj instanceof Error
      ? errorObj.message
      : 'unreachable'
    : null;

  const {
    data: rollups,
    warmingUp: rollupsWarming,
    error: rollupsErrorObj,
  } = useWarmingUpRetry<AuditRollupRow[]>(
    (signal) => api.auditRollups('week', signal),
    [],
    { queryKey: queryKeys.auditRollups('week') },
  );
  const rollupsError = rollupsErrorObj
    ? rollupsErrorObj instanceof Error
      ? rollupsErrorObj.message
      : 'unreachable'
    : null;

  const applyFilters = (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    setEntityFilter(entityDraft.trim());
    setActionFilter(actionDraft.trim());
    setEventTypeFilter(eventTypeDraft.trim());
  };
  const clearFilters = () => {
    setEntityDraft('');
    setActionDraft('');
    setEventTypeDraft('');
    setEntityFilter('');
    setActionFilter('');
    setEventTypeFilter('');
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
    <div className="surface mt-grid" id="audit">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4">Audit explorer</div>
          <div className="muted fs-12">
            Filter the Lakebase ledger by borrower/approval id, action, or event type.
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
          <Chip variant={error ? 'warning' : filtersActive ? 'success' : 'neutral'}>
            {error
              ? 'reconnecting'
              : updating
                ? 'updating'
                : filtersActive
                  ? events === null
                    ? 'filtering…'
                    : `${events.length} matching`
                  : 'last 25'}
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
        <form className="filter-row" onSubmit={applyFilters}>
          <label className="filter-row__group">
            <span className="field__label">ENTITY ID</span>
            <input
              className="admin-filter-input"
              value={entityDraft}
              onChange={(event) => setEntityDraft(event.target.value)}
              placeholder="B-... or approval UUID"
            />
          </label>
          <label className="filter-row__group">
            <span className="field__label">ACTION</span>
            <input
              className="admin-filter-input"
              value={actionDraft}
              onChange={(event) => setActionDraft(event.target.value)}
              placeholder="outreach.approve"
            />
          </label>
          <label className="filter-row__group">
            <span className="field__label">EVENT TYPE</span>
            <input
              className="admin-filter-input"
              value={eventTypeDraft}
              onChange={(event) => setEventTypeDraft(event.target.value)}
              placeholder="APPROVE"
            />
          </label>
          <div className="admin-filter-actions">
            <button type="submit" className="btn btn--default btn--sm">
              Apply filters
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={clearFilters}
              disabled={!filtersActive && !entityDraft && !actionDraft && !eventTypeDraft}
            >
              Clear
            </button>
          </div>
        </form>
        <div
          className={`audit-filter-chip-lane chip-row mt-3 ${filtersActive ? '' : 'is-empty'}`}
          aria-label="Applied audit filters"
          aria-hidden={!filtersActive}
        >
          {filtersActive && (
            <>
              {entityFilter.trim() && (
                <Chip variant="neutral">entity = {entityFilter.trim()}</Chip>
              )}
              {actionFilter.trim() && (
                <Chip variant="neutral">action = {actionFilter.trim()}</Chip>
              )}
              {eventTypeFilter.trim() && (
                <Chip variant="neutral">event = {eventTypeFilter.trim()}</Chip>
              )}
              <span className="muted fs-12">
                Showing the latest 25 rows that match the applied filters.
              </span>
            </>
          )}
        </div>
        {warmingUp && events === null && (
          <div className="mt-3">
            <WarmingUpBlock state={warmingUp} title="Audit explorer loading" compact />
          </div>
        )}
        {error && !warmingUp && (
          <div className="muted body fs-12 mt-3">
            Audit explorer unavailable: {error}
          </div>
        )}
        <div className="admin-rollups mt-3">
          <div className="h-5">Workflow events by week</div>
          {rollupsWarming && (
            <WarmingUpBlock state={rollupsWarming} title="Audit rollups loading" compact />
          )}
          {rollupsError && !rollupsWarming && (
            <div className="muted fs-12">Audit rollups unavailable: {rollupsError}</div>
          )}
          {!rollupsWarming && !rollupsError && (
            <div className="admin-rollups__grid">
              {(rollups ?? []).slice(0, 6).map((row) => (
                <div key={`${row.bucket_start}-${row.event_type}`} className="admin-rollup">
                  <span className="mono fs-12">{row.event_type}</span>
                  <strong>{row.event_count.toLocaleString()}</strong>
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
            tabIndex={0}
            aria-label="Audit event table"
            aria-busy={updating}
            data-status={statusLabel}
          >
            {events.length > 0 && (
              <table className="tbl" aria-label="Audit events">
                <thead>
                  <tr>
                    <th aria-label="Event details" />
                    <th>Action</th>
                    <th>Entity</th>
                    <th>Actor</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <AuditEventTableRow
                      key={event.event_id}
                      event={event}
                      expanded={expandedEventId === event.event_id}
                      copiedValue={copyState?.failed ? null : copyState?.value ?? null}
                      onToggle={() => setExpandedEventId((current) => (
                        current === event.event_id ? null : event.event_id
                      ))}
                      onCopy={(value, label) => void copyValue(value, label)}
                    />
                  ))}
                </tbody>
              </table>
            )}
            {events.length === 0 && (
              <div className="muted fs-12">No audit rows match these filters.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function auditEventDetailId(eventId: string): string {
  return `audit-event-${eventId.replace(/[^A-Za-z0-9_-]/g, '-')}`;
}

function auditRecordQuery(eventId: string): string {
  const escaped = eventId.replace(/'/g, "''");
  return `SELECT * FROM ${AUDIT_TABLE_CONTEXT} WHERE event_id = '${escaped}';`;
}

function formatAuditMetadataValue(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return 'Value unavailable';
  }
}

function AuditDetailValue({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="lineage-node">
      <div className="lineage-node__label">{label}</div>
      <div className="lineage-node__name">{value || 'Not recorded'}</div>
    </div>
  );
}

function AuditEventTableRow({
  event,
  expanded,
  copiedValue,
  onToggle,
  onCopy,
}: {
  event: AdminAuditEvent;
  expanded: boolean;
  copiedValue: string | null;
  onToggle: () => void;
  onCopy: (value: string, label: string) => void;
}) {
  const detailId = auditEventDetailId(event.event_id);
  const metadata = Object.entries(event.payload_json ?? {}).sort(([left], [right]) => (
    left.localeCompare(right)
  ));
  const evidenceIds = event.evidence_ids ?? [];

  return (
    <Fragment>
      <tr className={expanded ? 'is-expanded' : ''}>
        <td>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls={detailId}
            aria-label={`${expanded ? 'Collapse' : 'Expand'} audit event ${event.event_id}`}
            title={`${expanded ? 'Collapse' : 'Expand'} event details`}
          >
            <Icon name={expanded ? 'chevdown' : 'chevright'} size={13} />
          </button>
        </td>
        <td className="is-primary">
          <div>{event.action}</div>
          <div className="mono muted fs-11">{event.event_type ?? 'Unclassified'}</div>
        </td>
        <td>
          <div>{event.entity_type}</div>
          <div className="mono muted fs-11">{event.entity_id}</div>
        </td>
        <td>{event.actor}</td>
        <td>
          <time dateTime={event.created_at} title={event.created_at}>
            {formatAuditTimestamp(event.created_at)}
          </time>
        </td>
      </tr>
      {expanded && (
        <tr className="tbl__expand">
          <td colSpan={5}>
            <div className="tbl__expand-inner" id={detailId}>
              <div className="admin-rules-detail__hdr">
                <div>
                  <div className="h-5">Event details</div>
                  <div className="muted fs-12">{event.action}</div>
                </div>
                <div className="chip-row">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => onCopy(event.event_id, 'Event ID')}
                    aria-label={`Copy event ID ${event.event_id}`}
                    title="Copy event ID"
                  >
                    <Icon name="doc" size={12} />
                    {copiedValue === event.event_id ? 'Copied event ID' : 'Copy event ID'}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => onCopy(auditRecordQuery(event.event_id), 'Lakebase record query')}
                    aria-label={`Copy Lakebase query for audit event ${event.event_id}`}
                    title="Copy a query for this Lakebase row"
                  >
                    <Icon name="db" size={12} />
                    {copiedValue === auditRecordQuery(event.event_id)
                      ? 'Copied record query'
                      : 'Copy record query'}
                  </button>
                </div>
              </div>

              <div className="admin-rollups__grid mt-3">
                <AuditDetailValue label="Event ID" value={event.event_id} />
                <AuditDetailValue label="Request ID" value={event.request_id} />
                <AuditDetailValue label="Correlation ID" value={event.correlation_id} />
                <AuditDetailValue label="Masked subject reference" value={event.subject_clip} />
                <AuditDetailValue label="Subject segment" value={event.subject_segment} />
              </div>

              <div className="mt-3">
                <div className="field__label">EVIDENCE IDS</div>
                <div className="chip-row mt-2">
                  {evidenceIds.length > 0 ? evidenceIds.map((evidenceId) => (
                    <Chip key={evidenceId} variant="neutral" className="lineage-node__chip">
                      {evidenceId}
                    </Chip>
                  )) : (
                    <span className="muted fs-12">None recorded</span>
                  )}
                </div>
              </div>

              <div className="mt-3">
                <div className="field__label">REVIEWED METADATA</div>
                {metadata.length > 0 ? (
                  <div className="admin-rollups__grid mt-2">
                    {metadata.map(([key, value]) => (
                      <AuditDetailValue
                        key={key}
                        label={key}
                        value={formatAuditMetadataValue(value)}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="muted fs-12 mt-2">No metadata recorded.</div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function formatAuditTimestamp(iso: string): string {
  return formatTimestamp(iso, { withYear: false });
}
