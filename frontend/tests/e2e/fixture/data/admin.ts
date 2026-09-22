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

const DESTINATIONS: ActivationDestination[] = [
  { destination_key: 'salesforce_default', destination_type: 'salesforce', display_name: 'Salesforce (dry run)', status: 'dry_run', allowed_actions: ['stage'], updated_at: SNAPSHOT_AT },
  { destination_key: 'los_pos_default', destination_type: 'los_pos', display_name: 'LOS / POS', status: 'not_configured', allowed_actions: [], updated_at: null },
];

const CAPABILITIES: GrowthAgentCapabilityRow[] = [
  { key: 'genie_conversation', label: 'Genie Conversation API', ga: true, status: 'configured', claimable: true, detail: 'Space configured (fixture).' },
  { key: 'lakebase', label: 'Lakebase app state', ga: true, status: 'available', claimable: true, detail: 'Instance reachable (fixture).' },
  { key: 'agent_bricks', label: 'Agent Bricks supervisor', ga: false, status: 'not_provisioned', claimable: false, detail: 'Not provisioned in this workspace.' },
  { key: 'mlflow_tracing', label: 'MLflow traces / evals', ga: true, status: 'preview_mirror', claimable: false, detail: 'Production extension.' },
];

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
  fixture('GET', '/api/audit/rollups', () =>
    json<Array<{ bucket_start: string; event_type: string; event_count: number }>>(
      ['2026-07-08', '2026-07-09', '2026-07-10', '2026-07-11', '2026-07-12', '2026-07-13', '2026-07-14'].flatMap((date, index) => [
        { bucket_start: `${date}T00:00:00Z`, event_type: 'VIEW_LEADS', event_count: 40 + index * 6 },
        { bucket_start: `${date}T00:00:00Z`, event_type: 'APPROVE_OUTREACH', event_count: 8 + (index % 3) * 4 },
        { bucket_start: `${date}T00:00:00Z`, event_type: 'RUN_GENIE', event_count: 12 + (index % 4) * 5 },
      ]),
    ),
  ),
];
