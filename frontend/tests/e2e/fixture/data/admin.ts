/**
 * Administration fixtures: offer rules, source readiness, data operations,
 * capability readiness, activation destinations and the audit explorer.
 *
 * `RulesResponse`, `SourceRow` and `OperationsResponse` are declared privately
 * inside src/routes/admin-config.tsx and src/components/admin/
 * DataOperationsPanel.tsx, so they cannot be imported. The three interfaces
 * below mirror them field for field; if those components change shape, change
 * these with them. Every other payload is typed from `src/types`.
 */
import type { ActivationDestination, ActivationOutboxItem, ActivationSummary, DataEstateStatus } from '../../../../src/types';
import type { GrowthAgentCapabilityRow } from '../../../../src/types/growthAgent';
import type { AuditEventPage, AuditEventRow } from '../../../../src/lib/apiTypes';
import type { RefusalReportListResponse } from '../../../../src/lib/apiClients/refusalReports';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { LEADS } from './borrowers';
import { SNAPSHOT_AT, TOTALS } from './reference';

/** Mirror of `RulesResponse` in src/routes/admin-config.tsx. */
interface RulesResponse {
  offer_rules_version: string;
  rules_edited_at: string | null;
  thresholds: Array<{
    key: string;
    value: number;
    unit?: string | null;
    label?: string | null;
    description?: string | null;
    sort_order?: number | null;
    last_updated?: string | null;
  }>;
}

/** Mirror of `SourceRow` in src/routes/admin-config.tsx. */
interface SourceRow {
  name: string;
  status: DataEstateStatus;
  rows: number | null;
  last_updated: string | null;
  note: string;
}

/** Mirror of `OperationsResponse` in src/components/admin/DataOperationsPanel.tsx. */
interface OperationRun {
  run_id: number | null;
  life_cycle_state: string | null;
  result_state: string | null;
  state_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  run_page_url: string | null;
  active: boolean;
}

interface OperationsResponse {
  jobs: Array<{
    key: 'fred_rates' | 'silver_refresh' | 'gold_refresh' | 'lifecycle_sync';
    label: string;
    job_name: string;
    job_id: number | null;
    configured: boolean;
    description: string;
    run_order: number;
    cooldown_remaining_s?: number;
    latest_run: OperationRun | null;
    recent_runs?: OperationRun[];
  }>;
}

const OPERATION_JOBS: ReadonlyArray<readonly [OperationsResponse['jobs'][number]['key'], string]> = [
  ['fred_rates', 'FRED rate ingest'],
  ['silver_refresh', 'Silver refresh'],
  ['gold_refresh', 'Gold refresh'],
  ['lifecycle_sync', 'Lifecycle sync'],
];

const AUDIT_ACTIONS = ['APPROVE_OUTREACH', 'VIEW_LEADS', 'RUN_GENIE', 'DRAFT_OUTREACH'] as const;

const AUDIT_EVENTS: AuditEventRow[] = LEADS.slice(0, 12).map((lead, index) => {
  const action = AUDIT_ACTIONS[index % AUDIT_ACTIONS.length];
  return {
    event_id: `evt-fixture-${String(index).padStart(4, '0')}`,
    actor: index % 3 === 0 ? 'approver@summit.example' : 'analyst@summit.example',
    action,
    entity_type: 'borrower',
    entity_id: lead.borrower_id,
    payload_json: { offer_code: lead.recommended_offer_code ?? null },
    evidence_ids: lead.evidence_ids.slice(0, 1),
    created_at: `2026-07-14T11:${String(10 + index).padStart(2, '0')}:00Z`,
    event_type: action,
    subject_clip: lead.clip,
    subject_segment: lead.segment_codes[0],
    request_id: `req-fixture-${index}`,
    correlation_id: `corr-fixture-${index}`,
  };
});

/** One `GET /api/audit/rollups` row (backend AuditRollupResponse). */
interface AuditRollupFixtureRow {
  bucket_start: string;
  event_type: string;
  group_by: 'event_type';
  group_key: string;
  event_count: number;
}

function rollup(date: string, eventType: string, eventCount: number): AuditRollupFixtureRow {
  return { bucket_start: `${date}T00:00:00Z`, event_type: eventType, group_by: 'event_type', group_key: eventType, event_count: eventCount };
}

/** Mirror of backend AuditFacetsResponse (GET /api/audit/facets, audit-free). */
interface AuditFacetsFixture {
  event_types: Array<{ value: string; count: number }>;
  actions: Array<{ value: string; count: number }>;
  actors: Array<{ value: string; count: number }>;
  truncated: { event_types: boolean; actions: boolean; actors: boolean };
  since: string;
  until: string | null;
}

/** Mirror of backend AuditCountResponse (GET /api/audit/count, audit-free). */
interface AuditCountFixture {
  count: number;
  capped: boolean;
  cap: number;
}

/** Mirror of backend AuditExportReceipt (POST /api/audit/export-receipt). */
interface AuditExportReceiptFixture {
  audit_event_id: string;
  event_type: 'AUDIT_EXPORT';
  actor: string;
  row_count: number;
  csv_sha256: string;
  event_ids_sha256: string;
  filter_fingerprint: string;
  recorded_at: string;
}

function facetCounts(values: readonly string[]): Array<{ value: string; count: number }> {
  return values
    .map((value) => ({ value, count: AUDIT_EVENTS.filter((event) => [event.event_type, event.action, event.actor].includes(value)).length }))
    .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}

const AUDIT_FACETS: AuditFacetsFixture = {
  event_types: facetCounts(AUDIT_ACTIONS),
  actions: facetCounts(AUDIT_ACTIONS),
  actors: facetCounts(['approver@summit.example', 'analyst@summit.example']),
  truncated: { event_types: false, actions: false, actors: false },
  since: '2026-04-15T00:00:00Z',
  until: null,
};

function auditExportReceipt(body: unknown): AuditExportReceiptFixture {
  const declared = (body ?? {}) as { row_count?: unknown; csv_sha256?: unknown; event_ids_sha256?: unknown };
  const hex = (value: unknown, fill: string): string =>
    typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) ? value : fill.repeat(64);
  return {
    audit_event_id: '3b7c9f2e-5a41-4d8e-9c06-2f1a8b7d4e90',
    event_type: 'AUDIT_EXPORT',
    actor: 'approver@summit.example',
    row_count: typeof declared.row_count === 'number' ? declared.row_count : AUDIT_EVENTS.length,
    csv_sha256: hex(declared.csv_sha256, 'c'),
    event_ids_sha256: hex(declared.event_ids_sha256, 'e'),
    filter_fingerprint: 'f'.repeat(64),
    recorded_at: '2026-07-14T12:00:00Z',
  };
}

const DESTINATIONS: ActivationDestination[] = [
  // allowed_actions uses the governed activation vocabulary and is never
  // empty (backend/schemas/activation.py); the values mirror the
  // lakebase/schema.sql destination seed for each destination type.
  { destination_key: 'salesforce_default', destination_type: 'salesforce', display_name: 'Salesforce (dry run)', status: 'dry_run', allowed_actions: ['stage_lead', 'stage_campaign'], updated_at: SNAPSHOT_AT },
  { destination_key: 'los_pos_default', destination_type: 'los_pos', display_name: 'LOS / POS', status: 'not_configured', allowed_actions: ['stage_lead'], updated_at: null },
];

const CAPABILITIES: GrowthAgentCapabilityRow[] = [
  { key: 'genie_conversation', label: 'Genie Conversation API', ga: true, status: 'configured', claimable: true, detail: 'Space configured (fixture).' },
  { key: 'lakebase', label: 'Lakebase app state', ga: true, status: 'available', claimable: true, detail: 'Instance reachable (fixture).' },
  { key: 'agent_bricks', label: 'Agent Bricks supervisor', ga: false, status: 'not_provisioned', claimable: false, detail: 'Not provisioned in this workspace.' },
  { key: 'mlflow_tracing', label: 'MLflow traces / evals', ga: true, status: 'preview_mirror', claimable: false, detail: 'Production extension.' },
];

/**
 * The refusal-reports page (D-audit-reads-d): every server key, two reports
 * (one holding a consented question), synthetic `.example` reporters, UUID
 * report and audit ids and 32-hex Genie ids. Report metadata only: the
 * question is a separate, audited read the specs register themselves.
 */
export const REFUSAL_REPORT_WITH_TEXT_ID = '3c1d9e7a-5b2f-4c8d-9e6a-1f0b2c3d4e5f';
export const REFUSAL_REPORTS: RefusalReportListResponse = {
  items: [
    {
      report_id: REFUSAL_REPORT_WITH_TEXT_ID,
      reported_at: '2026-07-14T15:20:00Z',
      refusal_reason: 'unreviewed_criterion',
      reporter: 'lo.alpha@summit-mortgage.example',
      conversation_id: '01f13d4968af1b249dc388fd5b18b195',
      message_id: null,
      has_text: true,
      text_expires_at: '2026-10-12T15:20:00Z',
      audit_event_id: '8a7b6c5d-4e3f-4a2b-9c1d-0e9f8a7b6c5d',
    },
    {
      report_id: '9f8e7d6c-5b4a-4321-8fed-cba987654321',
      reported_at: '2026-07-13T10:05:00Z',
      refusal_reason: 'protected_class',
      reporter: 'lo.bravo@summit-mortgage.example',
      conversation_id: '01f13d4a0b7c1e5f8a2b3c4d5e6f7a8b',
      message_id: '01f13d4b1c2d3e4f5a6b7c8d9e0f1a2b',
      has_text: false,
      text_expires_at: null,
      audit_event_id: '1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e',
    },
  ],
  family_counts: [
    { refusal_reason: 'protected_class', count: 1 },
    { refusal_reason: 'unreviewed_criterion', count: 1 },
  ],
  next_cursor: null,
};

export const adminFixtures: FixtureEntry[] = [
  fixture('GET', '/api/admin/rules', () =>
    json<RulesResponse>({
      offer_rules_version: 'fixture-v1',
      rules_edited_at: SNAPSHOT_AT,
      thresholds: [
        { key: 'min_spread_bps', value: 75, unit: 'bps', label: 'Minimum rate spread', description: 'Lien rate minus par required to pass the refi economics screen.', sort_order: 1, last_updated: SNAPSHOT_AT },
        { key: 'min_equity_pct', value: 15, unit: 'pct', label: 'Minimum equity', description: 'AVM-based equity required for a refinance offer.', sort_order: 2, last_updated: SNAPSHOT_AT },
        { key: 'heloc_min_equity_pct', value: 40, unit: 'pct', label: 'HELOC equity threshold', description: 'Equity required for the HELOC branch.', sort_order: 3, last_updated: SNAPSHOT_AT },
        { key: 'par_rate', value: 0.04875, unit: 'rate_fraction', label: 'Par refinance rate', description: 'FRED 30-year conforming benchmark.', sort_order: 4, last_updated: SNAPSHOT_AT },
      ],
    }),
  ),
  fixture('GET', '/api/admin/sources', () =>
    json<SourceRow[]>([
      { name: 'Public Records (Deed & Mortgage)', status: 'live', rows: 1840000, last_updated: SNAPSHOT_AT, note: 'Cotality share' },
      { name: 'Voluntary Lien', status: 'live', rows: 1720000, last_updated: SNAPSHOT_AT, note: 'Cotality share' },
      { name: 'Owner Link', status: 'live', rows: 910000, last_updated: SNAPSHOT_AT, note: 'Cotality share' },
      { name: 'AVM', status: 'live', rows: 1840000, last_updated: SNAPSHOT_AT, note: 'Cotality share' },
      { name: 'MLS Listings', status: 'configured_empty', rows: 0, last_updated: null, note: 'Share configured; no rows yet' },
      { name: 'Building Permits', status: 'roadmap', rows: null, last_updated: null, note: 'Pending share' },
      { name: 'Borrower contact fields', status: 'demo_synthetic', rows: TOTALS.addressable, last_updated: SNAPSHOT_AT, note: 'Synthetic only' },
    ]),
  ),
  fixture('GET', '/api/admin/operations', () =>
    json<OperationsResponse>({
      jobs: OPERATION_JOBS.map(([key, label], index) => ({
        key,
        label,
        job_name: `mip_${key}`,
        job_id: 1001 + index,
        configured: true,
        description: `${label} job (fixture).`,
        run_order: index + 1,
        cooldown_remaining_s: 0,
        latest_run: {
          run_id: 5001 + index,
          life_cycle_state: 'TERMINATED',
          result_state: 'SUCCESS',
          state_message: null,
          started_at: '2026-07-14T06:00:00Z',
          ended_at: '2026-07-14T06:12:00Z',
          run_page_url: null,
          active: false,
        },
        recent_runs: [],
      })),
    }),
  ),
  fixture('GET', '/api/admin/capabilities', () => json<{ capabilities: GrowthAgentCapabilityRow[] }>({ capabilities: CAPABILITIES })),
  fixture('GET', '/api/activation/summary', () => json<ActivationSummary>({ destinations: DESTINATIONS, recent_outbox: [] })),
  fixture('GET', '/api/activation/destinations', () => json<ActivationDestination[]>(DESTINATIONS)),
  fixture('GET', '/api/activation/outbox', () => json<ActivationOutboxItem[]>([])),
  fixture('GET', '/api/audit/events', ({ query }) => {
    const limit = Number(query.get('limit') ?? AUDIT_EVENTS.length);
    return json<AuditEventRow[]>(AUDIT_EVENTS.slice(0, limit > 0 ? limit : AUDIT_EVENTS.length));
  }),
  fixture('GET', '/api/audit/events/page', () => json<AuditEventPage>({ items: AUDIT_EVENTS, next_cursor: null })),
  // Audit explorer (tables-10): admin-gated, audit-free reads and the
  // AUDIT_EXPORT receipt an explorer CSV download waits for.
  fixture('GET', '/api/audit/facets', () => json<AuditFacetsFixture>(AUDIT_FACETS)),
  fixture('GET', '/api/audit/count', () => json<AuditCountFixture>({ count: AUDIT_EVENTS.length, capped: false, cap: 50000 })),
  // Read only on an explicit "Show refusal reports" (D-audit-reads-d).
  fixture('GET', '/api/audit/refusal-reports', () => json<RefusalReportListResponse>(REFUSAL_REPORTS)),
  fixture('POST', '/api/audit/export-receipt', ({ body }) => json<AuditExportReceiptFixture>(auditExportReceipt(body))),
  // The default `group_by=event_type` rollup: the backend echoes the group in
  // group_by / group_key and fills event_type from it (backend/api/audit.py).
  fixture('GET', '/api/audit/rollups', () =>
    json<AuditRollupFixtureRow[]>(
      ['2026-07-08', '2026-07-09', '2026-07-10', '2026-07-11', '2026-07-12', '2026-07-13', '2026-07-14'].flatMap((date, index) => [
        rollup(date, 'VIEW_LEADS', 40 + index * 6),
        rollup(date, 'APPROVE_OUTREACH', 8 + (index % 3) * 4),
        rollup(date, 'RUN_GENIE', 12 + (index % 4) * 5),
      ]),
    ),
  ),
];
