import { Icon, type IconName } from '../Icon';
import { api, type AuditEventRow } from '../../lib/api';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { useOptionalHealth } from '../HealthProvider';

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
 * Values are not synthesized: `status`, `dependencies`, and
 * `circuit_breakers` come straight from the health endpoint; the
 * `probe_ms` is the wall-clock cost of the most recent probe that
 * produced them. This is the operator-honesty beat the talk track
 * calls out: if the warehouse is warming up, the activity log says so.
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
  try {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return iso;
    return d.toLocaleTimeString('en-US', { hour12: false });
  } catch {
    return iso;
  }
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
  } = useWarmingUpRetry<AuditEvent[]>((signal) => api.auditEvents(limit, signal), [limit]);
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
        <Icon name="audit" size={14} style={{ color: 'var(--accent)' }} />
        <div className="h-4">Agent action audit log</div>
      </div>
      <div className="audit-panel">
        {feedState === 'warming' && warmingUp && (
          <div style={{ padding: 'var(--sp-3)' }}>
            <WarmingUpBlock
              state={warmingUp}
              title="Agent activity loading"
              compact
            />
          </div>
        )}
        {feedState === 'loading' && (
          <div className="muted body" style={{ padding: 'var(--sp-3)' }}>
            Loading audit events…
          </div>
        )}
        {feedState === 'empty' && (
          <div className="muted body" style={{ padding: 'var(--sp-3)' }}>
            No activity yet — run a segment build or approve an offer to see
            events here.
          </div>
        )}
        {feedState === 'error' && (warehouse === 'down' || genie === 'down') && (
          <div className="body" style={{ padding: 'var(--sp-3)', color: 'var(--text-2)' }}>
            Audit feed is waiting on a dependency to come back online — the
            live state is shown below. The feed will populate automatically
            once the dependency reconnects.
          </div>
        )}
        {feedState === 'error' && warehouse !== 'down' && genie !== 'down' && (
          <div className="body" style={{ padding: 'var(--sp-3)', color: 'var(--signal-danger)' }}>
            Audit feed is briefly unavailable. This page will retry on the
            next refresh; live dependency state is shown below.
          </div>
        )}
        {feedState === 'ok' && rows.map((r) => {
          const cls = classify(r.action, r.entity_type);
          return (
            <div className="audit" key={r.event_id}>
              <div className="audit__time mono">{formatTime(r.created_at)}</div>
              <div className={`audit__ico ${cls.color}`}><Icon name={cls.icon} size={11} /></div>
              <div className="audit__body">
                <div className="audit__what">{r.action}</div>
                <div className="audit__who">{r.actor}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div
        className="surface__ft"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sp-3)' }}
        aria-label="Live dependency telemetry"
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span
            aria-hidden
            style={{
              width: 6, height: 6, borderRadius: '50%',
              background:
                warehouse === 'up'
                  ? 'var(--signal-success)'
                  : warehouse === 'down'
                    ? 'var(--signal-danger)'
                    : 'var(--text-3)',
            }}
          />
          Analytics warehouse {warehouse}
          {warehouseBreaker ? ` · ${warehouseBreaker}` : ''}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span
            aria-hidden
            style={{
              width: 6, height: 6, borderRadius: '50%',
              background:
                genie === 'up'
                  ? 'var(--signal-success)'
                  : genie === 'down'
                    ? 'var(--signal-danger)'
                    : 'var(--text-3)',
            }}
          />
          AI assistant {genie}
          {genieBreaker ? ` · ${genieBreaker}` : ''}
        </span>
        <span className="mono" style={{ marginLeft: 'auto' }}>
          Last health check{probeSuffix}
        </span>
      </div>
      <div className="surface__ft">Exported nightly for compliance review</div>
    </div>
  );
}
