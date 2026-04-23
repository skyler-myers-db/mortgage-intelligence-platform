import { useEffect, useState } from 'react';
import { Icon, type IconName } from '../Icon';
import { api, type AuditEventRow } from '../../lib/api';

/**
 * AgentActivityLog — prototype `.audit` BEM. Pulls events from
 * GET /api/audit/events (already exists). When the feed is empty or the
 * fetch fails, we render an honest empty state rather than mock-data
 * filler so presenters never mistake a dead backend for a live run.
 * Icon + color keyed off action verb so Approvals stand out green,
 * rejects red, Genie asks amber.
 *
 * The footer renders a live telemetry strip built from /api/health —
 * warehouse + Genie dependency state and a monotonic wall-clock probe
 * latency. Values are not synthesized: `status`, `dependencies`, and
 * `circuit_breakers` come straight from the health endpoint; the
 * `probe_ms` is the wall-clock cost of the single fetch that produced
 * them. This is the operator-honesty beat the talk track calls out:
 * if the warehouse is warming up, the activity log says so.
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
// Health telemetry — polled from /api/health every 30s. All three values
// (dep state, breaker state, probe_ms) are real measurements; nothing
// here is synthesized.
// ---------------------------------------------------------------------

type DepState = 'up' | 'down' | 'unknown';
type BreakerState = 'closed' | 'open' | 'half_open' | 'unknown';

interface HealthSnapshot {
  warehouse: DepState;
  genie: DepState;
  warehouse_breaker: BreakerState;
  genie_breaker: BreakerState;
  probe_ms: number | null;
  fetched_at: string;
}

const EMPTY_HEALTH: HealthSnapshot = {
  warehouse: 'unknown',
  genie: 'unknown',
  warehouse_breaker: 'unknown',
  genie_breaker: 'unknown',
  probe_ms: null,
  fetched_at: '',
};

function breakerLabel(b: BreakerState): string | null {
  // A closed breaker is the happy path; we only surface a friendly
  // status suffix when the dependency is degraded, so the strip stays
  // quiet at rest. Copy is buyer-facing — we avoid infra jargon like
  // "tripped" / "open breaker" in favor of plain English.
  if (b === 'open') return 'reconnecting';
  if (b === 'half_open') return 'reconnecting';
  return null;
}

type FeedState = 'loading' | 'empty' | 'error' | 'ok';

export function AgentActivityLog({ limit = 12 }: { limit?: number }) {
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [feedState, setFeedState] = useState<FeedState>('loading');
  const [health, setHealth] = useState<HealthSnapshot>(EMPTY_HEALTH);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Route through api.ts so 503s with `retryable: true` follow
        // the shared exponential-backoff cadence rather than falling
        // straight into the "unavailable" state. Hole-finder #4,
        // 2026-04-23.
        const data = await api.auditEvents(limit);
        if (cancelled) return;
        setRows(data);
        setFeedState(data.length > 0 ? 'ok' : 'empty');
      } catch {
        if (cancelled) return;
        setRows([]);
        setFeedState('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [limit]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const t0 = performance.now();
      // api.health() never throws — it returns `{ status:
      // 'unreachable', mode: 'unknown', dependencies: {} }` on failure
      // so "unknown" flows through the UI without `catch` bookkeeping.
      const body = await api.health();
      const elapsed = Math.round(performance.now() - t0);
      if (cancelled) return;
      setHealth({
        warehouse: (body.dependencies?.warehouse as DepState) ?? 'unknown',
        genie: (body.dependencies?.genie as DepState) ?? 'unknown',
        warehouse_breaker:
          (body.circuit_breakers?.warehouse as BreakerState) ?? 'unknown',
        genie_breaker:
          (body.circuit_breakers?.genie as BreakerState) ?? 'unknown',
        probe_ms: elapsed,
        fetched_at: new Date().toISOString(),
      });
    };
    void poll();
    const id = window.setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const warehouseBreaker = breakerLabel(health.warehouse_breaker);
  const genieBreaker = breakerLabel(health.genie_breaker);
  const probeSuffix = health.probe_ms != null ? ` · ${health.probe_ms} ms` : '';

  return (
    <div className="surface">
      <div className="surface__hdr">
        <Icon name="audit" size={14} style={{ color: 'var(--accent)' }} />
        <div className="h-4">Agent action audit log</div>
      </div>
      <div className="audit-panel">
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
        {feedState === 'error' && (
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
                health.warehouse === 'up'
                  ? 'var(--signal-success)'
                  : health.warehouse === 'down'
                    ? 'var(--signal-danger)'
                    : 'var(--text-3)',
            }}
          />
          Analytics warehouse {health.warehouse}
          {warehouseBreaker ? ` · ${warehouseBreaker}` : ''}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span
            aria-hidden
            style={{
              width: 6, height: 6, borderRadius: '50%',
              background:
                health.genie === 'up'
                  ? 'var(--signal-success)'
                  : health.genie === 'down'
                    ? 'var(--signal-danger)'
                    : 'var(--text-3)',
            }}
          />
          AI assistant {health.genie}
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
