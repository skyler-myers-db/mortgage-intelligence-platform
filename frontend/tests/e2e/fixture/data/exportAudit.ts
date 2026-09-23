/**
 * Export-audit lane fixtures (audit tables-08, flow-04, tables-10): the
 * LEAD_EXPORT receipt a CSV download waits for, and an audit page handler
 * that honours the explorer's filters so URL round trips and deep links can
 * be asserted. Registered per test with `mockApi.register(...)`, never in the
 * default registry: the receipt is a WRITE and each test owns its timing.
 *
 * Synthetic only: masked `B-` ids, `summit-mortgage.example` staff addresses.
 */
import type { AuditEventPage, AuditEventRow } from '../../../../src/lib/apiTypes';
import type { LeadExportReceipt, LeadExportReceiptRequest } from '../../../../src/lib/apiClients/leadExport';
import { json, type FixtureReply, type FixtureRequest } from '../mockApi';

/** A Lakebase-shaped audit id (UUID) for the receipt this lane's tests mint. */
export const EXPORT_RECEIPT_ID = '5f0c2a8e-4b1d-4c3e-9a70-0000000000a1';
export const EXPORT_ACTOR = 'approver@summit-mortgage.example';
export const RECORDED_AT = '2026-07-14T15:00:00Z';

export function leadExportReceiptFor(declaration: LeadExportReceiptRequest): LeadExportReceipt {
  return {
    audit_event_id: EXPORT_RECEIPT_ID,
    event_type: 'LEAD_EXPORT',
    actor: EXPORT_ACTOR,
    scope: declaration.scope,
    row_count: declaration.row_count,
    csv_sha256: declaration.csv_sha256,
    borrower_ids_sha256: declaration.borrower_ids_sha256,
    filter_fingerprint: 'f'.repeat(64),
    recorded_at: RECORDED_AT,
  };
}

/** The LEAD_EXPORT ledger row the receipt above stands for. */
export const LEAD_EXPORT_ROW: AuditEventRow = {
  event_id: EXPORT_RECEIPT_ID,
  actor: EXPORT_ACTOR,
  action: 'lead_queue.export',
  entity_type: 'lead_queue',
  entity_id: 'export-selected-2',
  payload_json: { export_scope: 'selected', exported_row_count: 2 },
  evidence_ids: [],
  created_at: RECORDED_AT,
  event_type: 'LEAD_EXPORT',
  subject_clip: null,
  subject_segment: null,
  request_id: 'req-export-0001',
  correlation_id: 'corr-export-0001',
};

function row(index: number, eventType: string, action: string, actor: string, day: string): AuditEventRow {
  return {
    event_id: `evt-explorer-${String(index).padStart(4, '0')}`,
    actor,
    action,
    entity_type: 'borrower',
    entity_id: `B-EXPLORER0000${index}`,
    payload_json: {},
    evidence_ids: [],
    created_at: `${day}T13:${String(10 + index).padStart(2, '0')}:00Z`,
    event_type: eventType,
    subject_clip: null,
    subject_segment: null,
    request_id: `req-explorer-${index}`,
    correlation_id: index % 2 === 0 ? 'corr-explorer-even' : `corr-explorer-${index}`,
  };
}

/** A small ledger with distinct actors, days, codes and one shared correlation id. */
export const EXPLORER_ROWS: readonly AuditEventRow[] = [
  LEAD_EXPORT_ROW,
  row(1, 'APPROVE', 'outreach.approve', EXPORT_ACTOR, '2026-07-14'),
  row(2, 'VIEW_LEADS', 'leads.view', 'analyst@summit-mortgage.example', '2026-07-13'),
  row(3, 'OUTREACH_REJECT', 'outreach.reject', EXPORT_ACTOR, '2026-07-10'),
  row(4, 'RUN_GENIE', 'genie.ask', 'analyst@summit-mortgage.example', '2026-07-02'),
];

function inWindow(createdAt: string, since: string | null, until: string | null): boolean {
  const at = Date.parse(createdAt);
  if (since && at < Date.parse(since)) return false;
  if (until && at > Date.parse(until)) return false;
  return true;
}

/**
 * `GET /api/audit/events/page` that filters the way the API does, on exactly
 * the parameters the explorer sends. Every request is recorded so a test can
 * assert what reached the wire.
 */
export function filteringAuditPage(requests: URLSearchParams[]) {
  return ({ query }: FixtureRequest): FixtureReply<AuditEventPage> => {
    requests.push(new URLSearchParams(query));
    const eventId = query.get('event_id');
    const actor = query.get('actor');
    const eventType = query.get('event_type');
    const correlation = query.get('correlation_id');
    const items = EXPLORER_ROWS.filter((event) => (
      (!eventId || event.event_id === eventId)
      && (!actor || event.actor === actor)
      && (!eventType || event.event_type === eventType)
      && (!correlation || event.correlation_id === correlation)
      && inWindow(event.created_at, query.get('since'), query.get('until'))
    ));
    return json<AuditEventPage>({ items, next_cursor: null });
  };
}
