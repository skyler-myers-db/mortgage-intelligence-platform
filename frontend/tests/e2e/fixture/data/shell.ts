/**
 * App-shell fixtures: the five calls every route makes on boot (session,
 * workspace, health, config options, footprint) plus the Console's recent
 * activity feed.
 */
import type { ConfigOptions, SessionResponse, WorkspaceState } from '../../../../src/types';
import type { ActorAuditEventPage, HealthPayload } from '../../../../src/lib/apiTypes';
import type { FollowupSummary } from '../../../../src/lib/apiClients/followUpsSummary';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry, type MockApi } from '../mockApi';
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

/**
 * Well-formed fixture actor keys (`actor_` + 16 lowercase hex): the shell
 * trusts a health or session key only in that shape (src/lib/healthTrust).
 * Literals, not imports: this directory may not import runtime src. Same
 * values as src/test/actorKeys.ts ACTOR_A / ACTOR_B.
 */
export const FIXTURE_ACTOR_A = 'actor_aaaaaaaaaaaaaaaa';
export const FIXTURE_ACTOR_B = 'actor_bbbbbbbbbbbbbbbb';
/**
 * The default fixtures' signed-in actor (D-identity-review-a4): a REAL key,
 * so the actor gate opens for an owner and the app sends X-MIP-Actor-Key, as
 * it does live. SESSION, HEALTH_OK and every default session variant carry
 * this same key (a different one would read as a proven change at the first
 * probe); its actor_email is non-null exactly as the real /session pairs them.
 */
export const FIXTURE_DEFAULT_ACTOR = 'actor_dddddddddddddddd';

export const HEALTH_OK: HealthPayload = {
  status: 'ok',
  mode: 'live',
  dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
  circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
  actor_cache_key: FIXTURE_DEFAULT_ACTOR,
  actor_cache_key_aliases: [],
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

/**
 * The signed-in fixture actor. `lender_name` and `rum_enabled` are the same
 * settings values CONFIG_OPTIONS carries (delivery-07: the tenant label and
 * the RUM gate ride the session call), so a helper that turns RUM on turns it
 * on in both, as the one backend setting does.
 */
export const SESSION: SessionResponse = {
  can_access_admin: true,
  can_approve: true,
  // An admin reads the audit ledger by the same decision (D-audit-reads-c3);
  // presenter mode is the customer default (D-shell-deviations-e1).
  can_read_audit: true,
  presenter_mode: false,
  actor_email: 'approver@summit-mortgage.example',
  lender_name: LENDER_NAME,
  rum_enabled: false,
  // The deploy payload's default (D-audit-reads-d): the refusal card offers
  // "Report with my question".
  refusal_text_capture_enabled: true,
  // The same key as HEALTH_OK (the session seed and every probe agree); the
  // SPA reads rotation aliases from /health only, the real /session sends [].
  actor_cache_key: FIXTURE_DEFAULT_ACTOR,
  actor_cache_key_aliases: [],
};

/**
 * One shared actor variable for a spec: GET /api/session and GET /api/health
 * both report `key()` (null: nobody), read per request, so flipping the
 * variable moves the next probe, the next page load and a new tab together.
 * The session keeps the real invariant (actor_email null exactly when the key
 * is); `aliases` names the rotation aliases a probe carries (health only, the
 * one place the SPA reads them).
 */
export function serveActor(mockApi: MockApi, key: () => string | null, aliases?: () => string[]): void {
  mockApi.register('GET', '/api/session', () => {
    const k = key();
    const actor_email = k === null ? null : SESSION.actor_email;
    return json<SessionResponse>({ ...SESSION, actor_cache_key: k, actor_cache_key_aliases: [], actor_email });
  });
  mockApi.register('GET', '/api/health', () => {
    const k = key();
    return json<HealthPayload>({
      ...HEALTH_OK,
      actor_cache_key: k ?? undefined,
      actor_cache_key_aliases: k === null ? undefined : (aliases?.() ?? []),
    });
  });
}

export const shellFixtures: FixtureEntry[] = [
  fixture('GET', '/api/session', () => json<SessionResponse>(SESSION)),
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
  // The rail's Follow-ups poll (D-shell-deviations-a): SESSION names an actor,
  // so every route sends it. Not eligible by default: no rail item, no list
  // read; data/followUps.ts registers the eligible actor's bodies.
  fixture('GET', '/api/sales/followups/summary', () => json<FollowupSummary>({ eligible: false, next_due_at: null })),
];

/**
 * Session variants for the shell-navigation lane (W5c w5-shell-nav-followups):
 * presenter mode on (the M1-M4 roadmap rail slots show, D-shell-deviations-e2),
 * a read-only auditor (Audit in the tools cluster) and a plain workspace user
 * (neither Audit nor Admin). Synthetic identity only.
 */
export const PRESENTER_SESSION: SessionResponse = {
  ...SESSION,
  presenter_mode: true,
  actor_display_name: null,
  role_labels: ['Administrator', 'Approver'],
};
export const AUDITOR_ONLY_SESSION: SessionResponse = {
  ...SESSION,
  can_access_admin: false,
  can_approve: false,
  can_read_audit: true,
  actor_email: 'auditor@summit-mortgage.example',
  actor_display_name: null,
  role_labels: ['Auditor'],
};
export const WORKSPACE_USER_SESSION: SessionResponse = {
  ...SESSION,
  can_access_admin: false,
  can_read_audit: false,
  actor_email: 'officer@summit-mortgage.example',
  actor_display_name: null,
  role_labels: ['Approver'],
};

export function contractSamples(): ContractSample[] {
  const sessions: Array<[string, SessionResponse]> = [
    ['PRESENTER_SESSION', PRESENTER_SESSION],
    ['AUDITOR_ONLY_SESSION', AUDITOR_ONLY_SESSION],
    ['WORKSPACE_USER_SESSION', WORKSPACE_USER_SESSION],
  ];
  return sessions.map(([name, body]) => ({ source: `data/shell.ts#${name}`, method: 'GET', pattern: '/api/session', body }));
}
