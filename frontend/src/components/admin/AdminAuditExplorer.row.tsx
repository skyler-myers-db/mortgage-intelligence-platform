/**
 * One audit ledger row in the explorer table, plus its forensic detail panel.
 * Moved out of AdminAuditExplorer.tsx (file-size gate) when the explorer
 * gained labels and deep links (audit flow-04 phase 1 / tables-10).
 *
 * Every row reads as a human label with its raw code kept beside it in a mono
 * chip; the detail panel's event id links to the explorer opened on that one
 * row, and its correlation id to every row of the same request. A masked
 * borrower entity (`B-` + 13) links to its Borrower 360 on click only (that
 * read writes its own VIEW_BORROWER row, so nothing here prefetches it).
 *
 * wow-stage-3: an EXPANDED decision row (the event types / actions the
 * receipt endpoint answers, isDecisionReceiptEvent) also reads its Decision
 * receipt back, compact and without the explorer link it would point at
 * itself. The receipt module (its existing shared chunk) loads on the first
 * expanded row, a static chunk and no read; the row asks that module whether
 * it is a decision, so the explorer imports nothing of it up front (a static
 * import would split the Lead Queue's shared chunk). GET /api/audit/receipt
 * writes no audit row, and a collapsed or non-decision row makes no receipt
 * request at all. The explorer itself stays admin-gated (app.tsx).
 */
import { Fragment, useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import type { AuditEventRow } from '../../lib/apiTypes';
import { auditCorrelationHref, auditEventHref } from '../../lib/auditLinks';
import { borrower360Path, isMaskedBorrowerId } from '../../lib/genieCellLinks';
import { formatTimestamp } from '../../lib/time';
import { auditEventCode, auditEventLabel } from './AdminAuditExplorer.labels';

type ReceiptModule = typeof import('../mortgage/DecisionReceipt');
let loadedReceiptModule: ReceiptModule | null = null;
// Module scope: the React Compiler cannot lower an import() inside a hook.
const loadReceiptModule = (): Promise<ReceiptModule> => import('../mortgage/DecisionReceipt');

/** The Decision receipt module once `wanted`; null while it loads or if it cannot. */
function useReceiptModule(wanted: boolean): ReceiptModule | null {
  const [module, setModule] = useState<ReceiptModule | null>(loadedReceiptModule);
  useEffect(() => {
    if (!wanted || module) return undefined;
    let live = true;
    loadReceiptModule().then(
      (loaded) => {
        loadedReceiptModule = loaded;
        if (live) setModule(loaded);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [wanted, module]);
  return module;
}

export const AUDIT_TABLE_CONTEXT = 'mip_app.action_audit';

export function formatAuditTimestamp(iso: string): string {
  return formatTimestamp(iso, { withYear: 'auto' });
}

function auditEventDetailId(eventId: string): string {
  return `audit-event-${eventId.replace(/[^A-Za-z0-9_-]/g, '-')}`;
}

export function auditRecordQuery(eventId: string): string {
  const escaped = eventId.replace(/'/g, "''");
  return `SELECT * FROM ${AUDIT_TABLE_CONTEXT} WHERE audit_id = '${escaped}';`;
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

function AuditDetailValue({ label, value, children }: {
  label: string;
  value?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="lineage-node">
      <div className="lineage-node__label">{label}</div>
      <div className="lineage-node__name">{children ?? (value || 'Not recorded')}</div>
    </div>
  );
}

export function AuditEventTableRow({
  event,
  expanded,
  copiedValue,
  onToggle,
  onCopy,
}: {
  event: AuditEventRow;
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
  const label = auditEventLabel(event);
  const code = auditEventCode(event);
  const receiptModule = useReceiptModule(expanded);
  const Receipt = receiptModule?.isDecisionReceiptEvent(event) ? receiptModule.DecisionReceipt : null;

  return (
    <Fragment>
      <tr className={expanded ? 'is-expanded' : ''} data-audit-event-id={event.event_id}>
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
          <div>{label}</div>
          <div className="chip-row">
            <Chip variant="neutral" className="mono" title="Raw event code in the ledger">{code}</Chip>
            {event.action !== code && <span className="mono muted fs-11">{event.action}</span>}
          </div>
        </td>
        <td>
          <div>{event.entity_type}</div>
          {isMaskedBorrowerId(event.entity_id) ? (
            <Link
              className="mono fs-11"
              to={borrower360Path(event.entity_id)}
              aria-label={`Open Borrower 360 for ${event.entity_id}`}
            >
              {event.entity_id}
            </Link>
          ) : (
            <div className="mono muted fs-11">{event.entity_id}</div>
          )}
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
                  <div className="muted fs-12">{label} · <span className="mono">{event.action}</span></div>
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
                <AuditDetailValue label="Event ID">
                  <Link
                    className="mono"
                    to={auditEventHref(event.event_id)}
                    aria-label={`Open audit event ${event.event_id} on its own`}
                  >
                    {event.event_id}
                  </Link>
                </AuditDetailValue>
                <AuditDetailValue label="Request ID" value={event.request_id} />
                <AuditDetailValue label="Correlation ID">
                  {event.correlation_id ? (
                    <Link
                      className="mono"
                      to={auditCorrelationHref(event.correlation_id)}
                      aria-label={`Show every audit event with correlation id ${event.correlation_id}`}
                    >
                      {event.correlation_id}
                    </Link>
                  ) : 'Not recorded'}
                </AuditDetailValue>
                <AuditDetailValue label="Masked subject reference" value={event.subject_clip} />
                <AuditDetailValue label="Subject segment" value={event.subject_segment} />
              </div>

              {Receipt && (
                <div className="mt-3" data-testid="audit-explorer-receipt">
                  <Receipt auditEventId={event.event_id} compact explorerLink={false} headingLevel={3} />
                </div>
              )}

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
