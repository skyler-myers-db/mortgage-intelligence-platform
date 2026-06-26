import { Icon, type IconName } from '../Icon';
import { api, type AuditEventRow } from '../../lib/api';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import { formatTimeOfDay } from '../../lib/time';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { useOptionalHealth } from '../HealthProvider';
import { queryKeys } from '../../lib/queryKeys';

/**
 * AgentActivityLog — prototype `.audit` BEM. Pulls events from
 * GET /api/audit/events (already exists). When the feed is empty or the
 * fetch fails, we render an honest empty state rather than mock-data
 * filler so presenters never mistake a dead backend for a live run.
 * Icon + color keyed off action verb so Approvals stand out green,
 * rejects red, Genie asks amber.
 *
 * The footer renders a live telemetry strip built from the shared
 * HealthProvider (round-2 hole-finder #21, 2026-04-23) — warehouse +
 * Genie dependency state and a monotonic wall-clock probe latency.
 * Values are not synthesized: `status` and `dependencies` come straight
 * from the browser health endpoint; optional `circuit_breakers` are only
 * appended when an ops-gated payload provides them. `probe_ms` is the
 * wall-clock cost of the most recent probe. This is the operator-honesty
 * beat the talk track calls out: if the warehouse is warming up, the
 * activity log says so.
 */

type AuditEvent = AuditEventRow;

type IconColor = '' | 'green' | 'amber' | 'red';

function classify(action: string, entityType: string): { icon: IconName; color: IconColor } {
  const a = action.toLowerCase();
  if (a.includes('approv')) return { icon: 'check', color: 'green' };
  if (a.includes('reject')) return { icon: 'cross', color: 'red' };
  if (a.includes('genie') || a.includes('ask')) return { icon: 'sparkle', color: 'amber' };
  if (a.includes('load')  || a.includes('pipeline')) return { icon: 'db', color: '' };
  if (a.includes('score') || a.includes('rank')) return { icon: 'bolt', color: 'amber' };
  if (a.includes('export')) return { icon: 'export', color: '' };
  if (a.includes('filter')) return { icon: 'filter', color: '' };
  if (entityType === 'session') return { icon: 'info', color: '' };
  return { icon: 'info', color: '' };
}

function formatTime(iso: string): string {
  // Dense 24h clock WITH an explicit timezone name; parses naive-UTC wire
  // strings as UTC instead of viewer-local (2026-06-11 audit fix).
  return formatTimeOfDay(iso);
}

/**
 * Detect "this row was attributed to a system identity, not a logged-in
 * user" and produce a friendly display label + tooltip. The backend
 * uses `system@databricks-apps` (or the legacy `unknown-actor@local`)
 * as the fallback when X-Forwarded-Email isn't present — typically for
 * background tasks, warm-up probes, or pre-OAuth requests. Without
 * this normalization the row reads as a mysterious user account.
 *
 * 2026-05-04 user feedback: "What is this agent action audit log? Who
 * is unknown-actor@local…?" — answered here by surfacing system
 * attribution explicitly with a tooltip explaining what it means.
 */
const SYSTEM_ACTOR_PATTERNS: Array<{ test: RegExp; label: string; tip: string }> = [
  {
    test: /^system@databricks-apps$/i,
    label: 'System (Databricks Apps)',
    tip: 'Action ran in a background task or warm-up probe — no logged-in user was attached to the request.',
  },
  {
    test: /^unknown-actor@local$/i,
    label: 'System (legacy)',
    tip: 'Action recorded before OAuth identity was wired through the request path. Pre-2026-05-04 fallback identity.',
  },
  {
    test: /^unknown-actor@untrusted-edge$/i,
    label: 'System (edge untrusted)',
    tip: 'Trust-forwarded-headers is disabled in this deployment, so the edge-claimed identity was rejected.',
  },
];

function actorDisplay(raw: string): { text: string; isSystem: boolean; tip: string | null } {
  for (const p of SYSTEM_ACTOR_PATTERNS) {
    if (p.test.test(raw)) return { text: p.label, isSystem: true, tip: p.tip };
  }
  return { text: raw, isSystem: false, tip: null };
}

// ---------------------------------------------------------------------
// Health telemetry — consumed from the shared HealthProvider. All three
// values (dep state, breaker state, probe_ms) are real measurements;
// nothing here is synthesized.
// ---------------------------------------------------------------------

type DepState = 'up' | 'down' | 'unknown';
type BreakerState = 'closed' | 'open' | 'half_open' | 'unknown';

function breakerLabel(b: BreakerState): string | null {
  // A closed breaker is the happy path; we only surface a friendly
  // status suffix when the dependency is degraded, so the strip stays
  // quiet at rest. Copy is buyer-facing — we avoid infra jargon like
  // "tripped" / "open breaker" in favor of plain English.
  if (b === 'open') return 'reconnecting';
  if (b === 'half_open') return 'reconnecting';
  return null;
}

type FeedState = 'loading' | 'empty' | 'error' | 'ok' | 'warming';

export function AgentActivityLog({ limit = 12 }: { limit?: number }) {
  const healthCtx = useOptionalHealth();
  const health = healthCtx?.health ?? null;
  const probeMs = healthCtx?.probeMs ?? null;
  const warehouse = (health?.dependencies?.warehouse as DepState) ?? 'unknown';
  const lakebase = (health?.dependencies?.lakebase as DepState) ?? 'unknown';
  const genie = (health?.dependencies?.genie as DepState) ?? 'unknown';
  const warehouseBreakerState =
    (health?.circuit_breakers?.warehouse as BreakerState) ?? 'unknown';
  const genieBreakerState =
    (health?.circuit_breakers?.genie as BreakerState) ?? 'unknown';

  // Cold-start warming-up — 6 retries / 5s apart on the first fetch.
  // Only the initial fetch runs today (single-mount, no steady poll);
  // if we add a polling cadence later, it should NOT route through this
  // hook — use a separate in-place refresh so a transient 503 on tick N
  // doesn't flip the whole feed to "warming" for 30s.
  const {
    data: auditData,
    warmingUp,
    error,
  } = useWarmingUpRetry<AuditEvent[]>(
    (signal) => api.auditEvents(limit, signal),
    [limit],
    { queryKey: queryKeys.auditEvents(['activity-log', limit]) },
  );
  const rows: AuditEvent[] = auditData ?? [];
  const feedState: FeedState = warmingUp
    ? 'warming'
    : error
      ? 'error'
      : auditData === null
        ? 'loading'
        : rows.length > 0
          ? 'ok'
          : 'empty';

  const warehouseBreaker = breakerLabel(warehouseBreakerState);
  const genieBreaker = breakerLabel(genieBreakerState);
  const probeSuffix = probeMs != null ? ` · ${probeMs} ms` : '';

  return (
    <div className="surface">
      <div className="surface__hdr">
        <Icon name="audit" size={14} className="icon-accent" />
        <div className="h-4">Agent action audit log</div>
      </div>
      <div className="audit-panel" tabIndex={0} aria-label="Agent action audit events">
        {feedState === 'warming' && warmingUp && (
          <div className="audit-panel__pad">
            <WarmingUpBlock
              state={warmingUp}
              title="Agent activity loading"
              compact
            />
          </div>
        )}
        {feedState === 'loading' && (
          <div className="muted body audit-panel__pad">
            Loading audit events…
          </div>
        )}
        {feedState === 'empty' && (
          <div className="muted body audit-panel__pad">
            No activity yet — run a segment build or approve an offer to see
            events here.
          </div>
        )}
        {/* 2026-05-04 fix (#4): the audit feed is backed by Lakebase
            (mip_app.action_audit). The prior conditions only checked
            warehouse + genie, so when Lakebase was the actual dep that
            went down (the common case — Lakebase is the audit store)
            the FE wrongly rendered the red "briefly unavailable" tile
            while showing "Analytics warehouse up · AI assistant up"
            below it. Now the muted "waiting" path fires whenever ANY
            of the three deps is down — including Lakebase. */}
        {feedState === 'error' && (warehouse === 'down' || lakebase === 'down' || genie === 'down') && (
          <div className="body audit-panel__message">
            Audit feed is waiting on a dependency to come back online — the
            live state is shown below. The feed will populate automatically
            once the dependency reconnects.
          </div>
        )}
        {feedState === 'error' && warehouse !== 'down' && lakebase !== 'down' && genie !== 'down' && (
          <div className="body audit-panel__message audit-panel__message--danger">
            Audit feed is briefly unavailable. This page will retry on the
            next refresh; live dependency state is shown below.
          </div>
        )}
        {feedState === 'ok' && rows.map((r) => {
          const cls = classify(r.action, r.entity_type);
          const actor = actorDisplay(r.actor);
          return (
            <div className="audit" key={r.event_id}>
              <div className="audit__time mono">{formatTime(r.created_at)}</div>
              <div className={`audit__ico ${cls.color}`}><Icon name={cls.icon} size={11} /></div>
              <div className="audit__body">
                <div className="audit__what">{r.action}</div>
                {/* System-attributed events render the friendly label
                    in muted/italic so a glance separates "user X did
                    this" from "the runtime did this on its own". The
                    raw actor string is kept in the tooltip for ops. */}
                <div
                  className={`audit__who ${actor.isSystem ? 'audit__who--system' : ''}`}
                  title={actor.tip ? `${actor.tip}\nRaw actor: ${r.actor}` : r.actor}
                >
                  {actor.text}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div
        className="surface__ft surface__ft--wrap"
        aria-label="Live dependency telemetry"
      >
        <span className="dependency-row">
          <span
            aria-hidden
            className={`dependency-dot ${
              warehouse === 'up'
                ? 'dependency-dot--up'
                : warehouse === 'down'
                  ? 'dependency-dot--down'
                  : ''
            }`}
          />
          Analytics warehouse {warehouse}
          {warehouseBreaker ? ` · ${warehouseBreaker}` : ''}
        </span>
        <span className="dependency-row">
          <span
            aria-hidden
            className={`dependency-dot ${
              genie === 'up'
                ? 'dependency-dot--up'
                : genie === 'down'
                  ? 'dependency-dot--down'
                  : ''
            }`}
          />
          AI assistant {genie}
          {genieBreaker ? ` · ${genieBreaker}` : ''}
        </span>
        <span className="mono surface__ft-spacer">
          Last health check{probeSuffix}
        </span>
      </div>
      <div className="surface__ft">Append-only and available for compliance export</div>
    </div>
  );
}
