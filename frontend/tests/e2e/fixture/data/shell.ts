/**
 * App-shell fixtures: the five calls every route makes on boot (session,
 * workspace, health, config options, footprint) plus the Console's recent
 * activity feed.
 */
import type { ConfigOptions, SessionResponse, WorkspaceState } from '../../../../src/types';
import type { ActorAuditEventPage, HealthPayload } from '../../../../src/lib/apiTypes';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { LEADS } from './borrowers';
import { LENDER_NAME, SNAPSHOT_DATE, STATES } from './reference';

type GeographyScope = NonNullable<ConfigOptions['geography_scope']>;

/**
 * Mirror of the un-exported `FootprintPayload` in
 * src/components/FootprintProvider.tsx. Importing that module would pull the
 * whole component graph into this directory's typecheck; the scope block is
 * typed from the exported `ConfigOptions['geography_scope']` instead.
 */
interface FootprintPayload {
  states: Array<{ state_code: string; state_name: string; display_order: number; is_default_state: boolean }>;
  geography_scope: GeographyScope | null;
  using_fallback: boolean;
}

export const GEOGRAPHY_SCOPE: GeographyScope = {
  state_count: STATES.length,
  county_count: STATES.length,
  zip_count: 412,
  snapshot_date: SNAPSHOT_DATE,
  source_table: 'mip.gold.geo_state_rollup',
  scope_label: `Cotality data coverage: ${STATES.length} counties across ${STATES.length} states`,
  counties: STATES.map((state) => ({
    state: state.code,
    fips_5: state.fips,
    county_name: state.county,
    addressable_borrowers: state.addressable,
  })),
};

export const HEALTH_OK: HealthPayload = {
  status: 'ok',
  mode: 'live',
  app_env: 'fixture',
  dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
  circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
  campaign_treatment_runtime: 'enabled',
  forced_degraded: null,
};

export const CONFIG_OPTIONS: ConfigOptions = {
  lender_name: LENDER_NAME,
  rum_enabled: false,
  geographies: ['All states', ...STATES.map((state) => state.name)],
  geographies_status: 'live',
  geography_scope: GEOGRAPHY_SCOPE,
  occupancy: ['All', 'Owner-occupied', 'Non-owner-occupied'],
  lien_status: ['Any', 'Open 1st lien', 'Open HELOC', 'Free & clear'],
  lender_relationships: ['All', 'Current customer', 'Former customer', 'Competitor customer'],
  products: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'],
  equity_thresholds: ['Any', '>= 15%', '>= 25%', '>= 40%'],
  target_lender_refs: ['All', 'Competitor A', 'Competitor B'],
  target_lender_refs_status: 'live',
};

export const shellFixtures: FixtureEntry[] = [
  fixture('GET', '/api/session', () => json<SessionResponse>({ can_access_admin: true })),
  fixture('GET', '/api/workspace', () => json<WorkspaceState>({ saved_leads: [], saved_drafts: [] })),
  fixture('GET', '/api/health', () => json<HealthPayload>(HEALTH_OK)),
  fixture('GET', '/api/config/options', () => json<ConfigOptions>(CONFIG_OPTIONS)),
  fixture('GET', '/api/config/footprint', () =>
    json<FootprintPayload>({
      states: STATES.map((state, index) => ({
        state_code: state.code,
        state_name: state.name,
        display_order: index + 1,
        is_default_state: index === 0,
      })),
      geography_scope: GEOGRAPHY_SCOPE,
      using_fallback: false,
    }),
  ),
  fixture('GET', '/api/audit/my-events', () =>
    json<ActorAuditEventPage>({
      items: LEADS.slice(0, 8).map((lead, index) => ({
        event_type: index % 2 === 0 ? 'VIEW_LEADS' : 'RUN_GENIE',
        entity_type: 'borrower',
        subject_id: lead.borrower_id,
        created_at: `2026-07-14T14:${String(40 - index * 4).padStart(2, '0')}:00Z`,
      })),
      next_cursor: null,
    }),
  ),
];
